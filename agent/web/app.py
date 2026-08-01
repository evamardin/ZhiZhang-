"""Flask Web 应用

提供财务数据的 REST API 和页面路由。
交易数据存储在 SQLite 中，支持导入、编辑、删除。
"""
import sys
import copy
from pathlib import Path

# 将 agent 目录加入 sys.path，以便导入父级模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, render_template, request, jsonify
from database import Database
from data_loader import Transaction, load_all
from config import get_data_dir, get_llm_config
from tools import execute_tool
from agent import FinancialAgent
import os
import json

# ── 全局状态 ────────────────────────────────────────

db = Database()

# AI Agent（延迟初始化）
_agent = None

# AI 建议缓存（跨页面保持）
_ai_suggest_cache = None

def get_agent():
    global _agent
    if _agent is None:
        llm_config = get_llm_config()
        if llm_config.get("api_key"):
            _agent = FinancialAgent(effective_transactions(), llm_config, db=db)
    return _agent

# 检查是否需要从 TSV 迁移数据
COUNT_BEFORE = 0
conn_check = db._connect()
try:
    COUNT_BEFORE = conn_check.execute("SELECT COUNT(*) as cnt FROM transactions").fetchone()["cnt"]
finally:
    conn_check.close()

if COUNT_BEFORE == 0:
    print("首次启动：从 TSV 文件迁移数据到 SQLite...")
    data_dir = get_data_dir()
    txns = load_all(data_dir)
    if txns:
        imported = db.import_transactions(txns, filename="首次 TSV 迁移", source="tsv_migration")
        print(f"  迁移 {imported} 条交易记录到数据库")
    else:
        print("  未找到 TSV 文件或无数据")


def _txn_dict_to_obj(d):
    """将 DB 返回的 dict 转为 Transaction 对象，兼容 analyzer 工具。"""
    return Transaction(
        id=d.get("id", ""),
        datetime=d.get("datetime", ""),
        date=d.get("date", ""),
        year_month=d.get("year_month", ""),
        amount=float(d.get("amount", 0)),
        direction=d.get("direction", ""),
        merchant=d.get("merchant", ""),
        description=d.get("description", ""),
        category=d.get("category", "待分类"),
        category_confidence=d.get("category_confidence", "pending"),
        source=d.get("source", ""),
        account=d.get("account", ""),
        status=d.get("status", ""),
        transaction_id=d.get("transaction_id", ""),
        is_duplicate=bool(d.get("is_duplicate", 0)),
    )


def effective_transactions(start_date="", end_date=""):
    """应用用户确认分类和商户规则，返回供统计使用的 Transaction 对象列表。
    start_date/end_date 可选，按交易日期（YYYY-MM-DD）过滤。
    """
    classifications = db.get_all_classifications()
    rules = db.get_category_rules(active_only=True)
    raw_txns = db.get_all_transactions()

    result = []
    for d in raw_txns:
        if d.get("is_duplicate"):
            continue
        if start_date and (d.get("date") or "") < start_date:
            continue
        if end_date and (d.get("date") or "") > end_date:
            continue
        item = _txn_dict_to_obj(d)

        # 应用商户规则
        matched_rule = next(
            (rule for rule in sorted(rules, key=lambda r: len(r["keyword"]), reverse=True)
             if rule["keyword"] and rule["keyword"] in item.merchant),
            None,
        )
        # 用户确认的分类优先
        if item.id in classifications:
            item.category = classifications[item.id]
            item.category_confidence = "high"
        elif matched_rule:
            item.category = matched_rule["category"]
            item.category_confidence = "high"
        result.append(item)
    return result


app = Flask(__name__)


# ══════════════════════════════════════════════════════
# 页面路由
# ══════════════════════════════════════════════════════

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/transactions")
def transactions_page():
    return render_template("transactions.html")


@app.route("/pending")
def pending_page():
    return render_template("pending.html")


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/chat")
def chat_page():
    """AI 对话页面"""
    return render_template("chat.html")


# ══════════════════════════════════════════════════════
# 分析 API（委托给 execute_tool）
# ══════════════════════════════════════════════════════

def _get_range_args():
    """从请求参数中读取日期范围（YYYY-MM-DD），返回 (start_date, end_date)"""
    return (
        (request.args.get("start_date") or "").strip(),
        (request.args.get("end_date") or "").strip(),
    )


@app.route("/api/summary")
def api_summary():
    year_month = request.args.get("year_month", "")
    start_date, end_date = _get_range_args()
    try:
        result = execute_tool(
            "get_monthly_summary",
            {"year_month": year_month, "start_date": start_date, "end_date": end_date},
            effective_transactions(start_date, end_date),
        )
        return jsonify(json.loads(result))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/categories")
def api_categories():
    year_month = request.args.get("year_month", "")
    start_date, end_date = _get_range_args()
    try:
        result = execute_tool(
            "get_category_breakdown",
            {"year_month": year_month, "start_date": start_date, "end_date": end_date},
            effective_transactions(start_date, end_date),
        )
        return jsonify(json.loads(result))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trend")
def api_trend():
    months = request.args.get("months", 6)
    try:
        months = int(months)
    except (ValueError, TypeError):
        months = 6
    start_date, end_date = _get_range_args()
    try:
        result = execute_tool(
            "get_spending_trend",
            {"num_months": months, "start_date": start_date, "end_date": end_date},
            effective_transactions(start_date, end_date),
        )
        return jsonify(json.loads(result))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/cashflow")
def api_cashflow():
    months = request.args.get("months", 3)
    try:
        months = int(months)
    except (ValueError, TypeError):
        months = 3
    current_balance_str = db.get_setting("current_balance", "0")
    try:
        current_balance = float(current_balance_str)
    except (ValueError, TypeError):
        current_balance = 0.0
    try:
        result = json.loads(execute_tool(
            "project_cashflow",
            {"current_balance": current_balance, "months": months},
            effective_transactions(),
        ))
        fixed_income = sum(float(item["amount"]) for item in db.get_fixed_items("income"))
        fixed_expense = sum(float(item["amount"]) for item in db.get_fixed_items("expense"))
        if fixed_income or fixed_expense:
            result["avg_monthly_income"] = round(result["avg_monthly_income"] + fixed_income, 2)
            result["avg_monthly_expense"] = round(result["avg_monthly_expense"] + fixed_expense, 2)
            balance = current_balance
            for projection in result["projections"]:
                projection["projected_income"] = result["avg_monthly_income"]
                projection["projected_expense"] = result["avg_monthly_expense"]
                projection["start_balance"] = round(balance, 2)
                balance += result["avg_monthly_income"] - result["avg_monthly_expense"]
                projection["end_balance"] = round(balance, 2)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/merchants")
def api_merchants():
    year_month = request.args.get("year_month", "")
    start_date, end_date = _get_range_args()
    limit = request.args.get("limit", 10)
    try:
        limit = int(limit)
    except (ValueError, TypeError):
        limit = 10
    try:
        result = execute_tool(
            "get_top_merchants",
            {"year_month": year_month if year_month else None,
             "start_date": start_date, "end_date": end_date, "limit": limit},
            effective_transactions(start_date, end_date),
        )
        return jsonify(json.loads(result))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
# 交易列表 API — 从 DB 查询
# ══════════════════════════════════════════════════════

@app.route("/api/transactions")
def api_transactions():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 50

    category = request.args.get("category")
    merchant = request.args.get("merchant")
    direction = request.args.get("direction")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    sort_by = request.args.get("sort_by", "datetime")
    sort_order = request.args.get("sort_order", "desc")

    items, total = db.query_transactions_db(
        category=category, merchant=merchant, direction=direction,
        start_date=start_date, end_date=end_date,
        sort_by=sort_by, sort_order=sort_order,
        page=page, per_page=per_page,
    )

    # 合并用户确认的分类
    txn_ids = [item["id"] for item in items]
    classifications = db.get_batch_classifications(txn_ids)

    output = []
    for item in items:
        confirmed_cat = classifications.get(item["id"])
        category_val = confirmed_cat if confirmed_cat else item["category"]
        output.append({
            "id": item["id"],
            "datetime": item["datetime"],
            "date": item["date"],
            "year_month": item["year_month"],
            "amount": item["amount"],
            "direction": {"income": "收入", "expense": "支出", "neutral": "中性"}.get(item["direction"], item["direction"]),
            "merchant": item["merchant"],
            "description": item["description"],
            "category": category_val,
            "category_confidence": item["category_confidence"],
            "source": item["source"],
            "account": item["account"],
            "status": item["status"],
        })

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": output,
    })


# ══════════════════════════════════════════════════════
# 交易 CRUD API
# ══════════════════════════════════════════════════════

@app.route("/api/merchants/batch-classify", methods=["POST"])
def api_merchant_batch_classify():
    """将同一商户的所有交易设置为某个分类，并添加分类规则"""
    data = request.get_json(force=True)
    merchant = (data.get("merchant") or "").strip()
    category = (data.get("category") or "").strip()
    if not merchant or not category:
        return jsonify({"error": "merchant 和 category 必填"}), 400

    try:
        # 1. 查找该商户所有交易
        items, _ = db.query_transactions_db(merchant=merchant, per_page=99999, exact_merchant=True)

        # 2. 批量设置分类
        count = 0
        for item in items:
            db.set_classification(item["id"], category)
            count += 1

        # 3. 添加/更新分类规则
        db.upsert_rule(merchant, category)

        # 3.5 知识沉淀：把新商户写回本体实例（越用越准闭环）
        try:
            from knowledge.query import upsert_merchant
            upsert_merchant(merchant, category)
        except Exception:
            pass

        # 4. 如果分类名含 /，自动创建父子分类
        if "/" in category:
            parts = category.split("/", 1)
            parent_name = parts[0].strip()
            child_name = parts[1].strip()
            if parent_name and child_name:
                try:
                    parent_id = None
                    for c in db.get_categories():
                        if c["name"] == parent_name and not c["parent_id"]:
                            parent_id = c["id"]
                            break
                    if not parent_id:
                        parent_id = db.add_category(parent_name)
                    # 检查子分类是否已存在
                    exists = any(c["name"] == child_name and c["parent_id"] == parent_id for c in db.get_categories())
                    if not exists:
                        db.add_category(child_name, parent_id)
                except Exception:
                    pass  # 分类创建失败不影响主流程

        return jsonify({"success": True, "count": count, "merchant": merchant, "category": category})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transactions/<transaction_id>", methods=["GET"])
def api_get_transaction(transaction_id):
    txn = db.get_transaction(transaction_id)
    if not txn:
        return jsonify({"error": "交易不存在"}), 404
    return jsonify({
        "id": txn["id"],
        "datetime": txn["datetime"],
        "date": txn["date"],
        "amount": txn["amount"],
        "direction": txn["direction"],
        "merchant": txn["merchant"],
        "description": txn["description"],
        "category": txn["category"],
        "account": txn["account"],
        "source": txn["source"],
    })


@app.route("/api/transactions/<transaction_id>", methods=["PUT"])
def api_update_transaction(transaction_id):
    """编辑交易字段"""
    data = request.get_json(force=True)
    allowed = {"amount", "merchant", "description", "account", "date", "datetime"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "没有需要更新的字段"}), 400
    if "amount" in updates:
        updates["amount"] = float(updates["amount"])
    try:
        db.update_transaction(transaction_id, **updates)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transactions/dedup/find", methods=["POST"])
def api_find_duplicates():
    """查找重复交易对"""
    try:
        data = request.get_json(force=True) or {}
        minutes = int(data.get("time_window", 1))
        pairs = db.find_duplicate_pairs(minutes)
        return jsonify({"pairs": pairs, "count": len(pairs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transactions/dedup/batch-delete", methods=["POST"])
def api_batch_delete_duplicates():
    """批量删除指定交易"""
    try:
        data = request.get_json(force=True)
        ids = data.get("ids", [])
        if not ids:
            return jsonify({"error": "请指定要删除的交易"}), 400
        count = db.batch_delete_transactions(ids)
        return jsonify({"deleted": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transactions/<transaction_id>", methods=["DELETE"])
def api_delete_transaction(transaction_id):
    """删除交易（软删除）"""
    try:
        db.delete_transaction(transaction_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transactions/<transaction_id>/restore", methods=["POST"])
def api_restore_transaction(transaction_id):
    """恢复已删除交易"""
    try:
        db.restore_transaction(transaction_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 分类操作 ──

@app.route("/api/transactions/<transaction_id>/category", methods=["DELETE"])
def api_reset_transaction_category(transaction_id):
    db.delete_classification(transaction_id)
    return jsonify({"success": True})


@app.route("/api/transactions/<transaction_id>/category", methods=["PUT"])
def api_update_transaction_category(transaction_id):
    """更新单条交易的分类，并自动创建商户规则"""
    data = request.get_json(force=True)
    category = data.get("category")
    if not category:
        return jsonify({"error": "缺少 category 字段"}), 400
    try:
        db.set_classification(transaction_id, category)

        # 从 DB 获取商户名
        txn = db.get_transaction(transaction_id)
        merchant_name = txn.get("merchant", "") if txn else ""
        if merchant_name:
            db.upsert_rule(merchant_name, category)
            # 知识沉淀：把新商户写回本体实例（越用越准闭环）
            try:
                from knowledge.query import upsert_merchant
                upsert_merchant(merchant_name, category)
            except Exception:
                pass
            # 将同商户全部交易应用该分类（精确匹配商户名）
            same_merchant = db.query_transactions_db(
                merchant=merchant_name, per_page=9999, exact_merchant=True
            )[0]
            for item in same_merchant:
                db.set_classification(item["id"], category)

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 标签操作 ──

@app.route("/api/tags", methods=["GET"])
def api_get_tags():
    """获取所有标签"""
    return jsonify(db.get_all_tags())


@app.route("/api/tags", methods=["POST"])
def api_add_tag():
    """新增标签"""
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "标签名不能为空"}), 400
    try:
        tag_id = db.add_tag(name, data.get("color", "#4299e1"))
        return jsonify({"success": True, "id": tag_id, "name": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tags/<tag_id>", methods=["PUT"])
def api_update_tag(tag_id):
    data = request.get_json(force=True)
    try:
        db.update_tag(tag_id, name=data.get("name"), color=data.get("color"))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tags/<tag_id>", methods=["DELETE"])
def api_delete_tag(tag_id):
    try:
        db.delete_tag(tag_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transactions/<transaction_id>/tags", methods=["GET"])
def api_get_transaction_tags(transaction_id):
    """获取某笔交易的所有标签"""
    tags = db.get_transaction_tags(transaction_id)
    return jsonify({"tags": tags})


@app.route("/api/transactions/<transaction_id>/tags", methods=["PUT"])
def api_set_transaction_tags(transaction_id):
    """全量设置交易标签"""
    data = request.get_json(force=True)
    tag_names = data.get("tags", []) or []
    try:
        db.set_transaction_tags(transaction_id, tag_names)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transactions/<transaction_id>/tags", methods=["POST"])
def api_add_transaction_tag(transaction_id):
    """给交易添加一个标签"""
    data = request.get_json(force=True)
    tag_name = data.get("name", "").strip()
    tag_id = data.get("tag_id")
    if not tag_name and not tag_id:
        return jsonify({"error": "name 或 tag_id 必填"}), 400
    try:
        db.add_transaction_tag(transaction_id, tag_name=tag_name, tag_id=tag_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transactions/<transaction_id>/tags/<tag_id>", methods=["DELETE"])
def api_remove_transaction_tag(transaction_id, tag_id):
    """移除交易的某个标签"""
    try:
        db.remove_transaction_tag(transaction_id, tag_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tags/summary", methods=["GET"])
def api_get_tag_summary():
    """获取所有标签的收支统计（可按日期范围过滤）"""
    start_date, end_date = _get_range_args()
    try:
        return jsonify(db.get_tag_summary(start_date=start_date, end_date=end_date))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tags/<tag_id>/transactions", methods=["GET"])
def api_get_transactions_by_tag(tag_id):
    """按标签查询交易"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    items, total = db.get_transactions_by_tag(tag_id, page=page, per_page=per_page)
    classifications = db.get_batch_classifications([i["id"] for i in items])
    output = []
    for item in items:
        confirmed_cat = classifications.get(item["id"])
        category_val = confirmed_cat if confirmed_cat else item["category"]
        output.append({
            "id": item["id"],
            "datetime": item["datetime"],
            "amount": item["amount"],
            "direction": {"income": "收入", "expense": "支出"}.get(item["direction"], item["direction"]),
            "merchant": item["merchant"],
            "description": item["description"],
            "category": category_val,
            "source": item["source"],
        })
    return jsonify({"items": output, "total": total, "page": page, "per_page": per_page})


# ══════════════════════════════════════════════════════
# 导入 API
# ══════════════════════════════════════════════════════

@app.route("/api/import", methods=["POST"])
def api_import_transactions():
    """上传 CSV/XLSX 文件并导入交易（先支持 JSON body 导入）"""
    data = request.get_json(force=True)
    txns_data = data.get("transactions", [])
    if not txns_data:
        return jsonify({"error": "缺少 transactions 字段"}), 400

    # 构建 Transaction 对象
    from data_loader import Transaction
    txns = []
    for i, item in enumerate(txns_data):
        txns.append(Transaction(
            id=item.get("id", f"import_{i}"),
            datetime=item.get("datetime", ""),
            date=item.get("date", ""),
            year_month=item.get("year_month", item.get("date", "")[:7]),
            amount=float(item.get("amount", 0)),
            direction=item.get("direction", "expense"),
            merchant=item.get("merchant", ""),
            description=item.get("description", ""),
            category=item.get("category", "待分类"),
            category_confidence=item.get("category_confidence", "pending"),
            source=item.get("source", ""),
            account=item.get("account", ""),
            status=item.get("status", ""),
            transaction_id=item.get("transaction_id", ""),
            is_duplicate=False,
        ))

    imported = db.import_transactions(txns, filename=data.get("filename", "手动导入"), source=data.get("source", "manual"))
    return jsonify({"success": True, "imported": imported})


@app.route("/api/import/batches", methods=["GET"])
def api_get_import_batches():
    """获取导入历史"""
    return jsonify(db.get_import_batches())


# ══════════════════════════════════════════════════════
# 待分类 API
# ══════════════════════════════════════════════════════

@app.route("/api/pending")
def api_pending():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 50

    classified_ids = set(db.get_all_classifications().keys())

    pending = [
        t for t in effective_transactions()
        if t.category == "待分类"
        and t.category_confidence != "high"
        and t.id not in classified_ids
        and (not start_date or (t.date or "") >= start_date)
        and (not end_date or (t.date or "") <= end_date)
    ]

    total = len(pending)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = pending[start:end]

    items = [{
        "id": t.id,
        "datetime": t.datetime,
        "amount": t.amount,
        "direction": t.direction,
        "merchant": t.merchant,
        "description": t.description,
        "source": t.source,
    } for t in page_items]

    # 批量获取标签
    txn_ids = [i["id"] for i in items]
    if txn_ids:
        tags_map = db.get_batch_tags(txn_ids)
        for item in items:
            item["tags"] = tags_map.get(item["id"], [])

    return jsonify({"total": total, "page": page, "per_page": per_page, "items": items})


@app.route("/api/pending/batch", methods=["POST"])
def api_pending_batch():
    data = request.get_json(force=True)
    classifications = data.get("classifications", {})
    if not classifications and data.get("ids") and data.get("category"):
        classifications = {str(txn_id): data["category"] for txn_id in data["ids"]}
    if data.get("all") and data.get("category"):
        classifications = {
            t.id: data["category"] for t in effective_transactions()
            if t.category == "待分类"
        }
    if not classifications:
        return jsonify({"error": "缺少 classifications 字段"}), 400

    count = 0
    for txn_id, category in classifications.items():
        if category:
            db.set_classification(txn_id, category)
            count += 1
            txn = db.get_transaction(txn_id)
            merchant_name = txn.get("merchant", "") if txn else ""
            if merchant_name:
                db.upsert_rule(merchant_name, category)
                # 知识沉淀：把新商户写回本体实例（越用越准闭环）
                try:
                    from knowledge.query import upsert_merchant
                    upsert_merchant(merchant_name, category)
                except Exception:
                    pass
                same_merchant = db.query_transactions_db(merchant=merchant_name, per_page=9999, exact_merchant=True)[0]
                for item in same_merchant:
                    db.set_classification(item["id"], category)

    return jsonify({"success": True, "count": count})


@app.route("/api/pending/suggest", methods=["POST"])
def api_pending_suggest():
    """使用 AI 对当前待分类交易批量建议分类，并识别可能的固定支出"""
    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()
    pending = [
        t for t in effective_transactions()
        if t.category == "待分类"
        and t.category_confidence != "high"
        and t.id not in set(db.get_all_classifications().keys())
        and (not start_date or (t.date or "") >= start_date)
        and (not end_date or (t.date or "") <= end_date)
    ]
    if not pending:
        return jsonify({"suggestions": [], "fixed_candidates": []})

    # 按商户名去重，减少 API 调用
    seen_merchants = {}
    for t in pending:
        m = (t.merchant or "").strip()
        if not m:
            continue
        if m not in seen_merchants or abs(t.amount) > abs(seen_merchants[m]["amount"]):
            seen_merchants[m] = {"merchant": m, "amount": t.amount, "count": 1, "sample_ids": [t.id]}
        else:
            seen_merchants[m]["count"] += 1
            seen_merchants[m]["sample_ids"].append(t.id)

    # 识别同一商户出现多次的 → 可能是固定支出
    frequency_map = {}
    for t in pending:
        m = (t.merchant or "").strip()
        if m:
            ym = t.datetime[:7] if t.datetime else ""
            if m not in frequency_map:
                frequency_map[m] = {}
            if ym:
                frequency_map[m][ym] = frequency_map[m].get(ym, 0) + 1

    fixed_candidates = []
    seen_fixed_merchants = set()
    for t in pending:
        m = (t.merchant or "").strip()
        if not m or m in seen_fixed_merchants:
            continue
        months_present = len(frequency_map.get(m, {}))
        total_occurrences = sum(frequency_map.get(m, {}).values())
        day = t.datetime[8:10] if t.datetime and len(t.datetime) >= 10 else "01"
        # 如果在 2+ 个月份中出现，或出现 3+ 次 → 可能是固定支出
        if months_present >= 2 or total_occurrences >= 3:
            fixed_candidates.append({
                "merchant": m,
                "amount": abs(t.amount),
                "day_of_month": int(day),
                "direction": t.direction,
                "occurrences": total_occurrences,
                "months": months_present,
                "sample_id": t.id,
            })
            seen_fixed_merchants.add(m)

    # 用 AI 建议分类：按商户汇总后调用，每批发 30 个商户
    merchant_samples = {}  # merchant -> {samples: [..], ids: [..]}
    for t in pending:
        m = (t.merchant or "").strip()
        key = m or (t.description or "")[:20]
        if not key:
            continue
        if key not in merchant_samples:
            merchant_samples[key] = {"merchant": m, "samples": [], "ids": []}
        merchant_samples[key]["ids"].append(t.id)
        if len(merchant_samples[key]["samples"]) < 2:
            merchant_samples[key]["samples"].append({
                "id": t.id,
                "amount": abs(t.amount),
                "direction": t.direction,
                "description": t.description or "",
            })

    suggestions = {}  # txn_id -> {category, reason}

    if merchant_samples:
        # 优先用 DB 设置，其次 .env（get_llm_config 内部会加载 .env）
        api_key = db.get_setting("api_key", "") or get_llm_config().get("api_key", "")
        if api_key:
            try:
                from openai import OpenAI
                base_url = db.get_setting("api_base_url", "https://api.deepseek.com/v1")
                model = db.get_setting("api_model", "deepseek-chat")
                client = OpenAI(base_url=base_url, api_key=api_key)

                merchant_list = list(merchant_samples.values())
                batch_size = 30

                for batch_start in range(0, len(merchant_list), batch_size):
                    batch = merchant_list[batch_start:batch_start + batch_size]

                    # 准备发给 AI 的数据：每个商户 + 典型样本
                    batch_items = []
                    for idx, ms in enumerate(batch):
                        batch_items.append({
                            "ref": f"m{batch_start + idx}",  # 索引用，避免商户名当 key 有问题
                            "merchant": ms["merchant"],
                            "direction": ms["samples"][0]["direction"],
                            "samples": ms["samples"],
                        })

                    prompt = f"""你是一个专业的财务分类助手。请根据商户名称、交易描述、金额和收支方向，为每笔交易选择最合适的分类，并推荐1-3个标签。

【可选分类列表】
支出类：餐饮、交通、日用百货、购物、娱乐、学习、住房、医疗健康、旅行、通讯、投资、其他
收入类：工资、兼职、理财收入、转账、退款、其他收入

【分类规则】
- 餐饮：吃饭、奶茶、咖啡、外卖、餐厅、食堂、小吃
- 交通：地铁、公交、打车、火车、机票、加油、停车
- 日用百货：超市、便利店、日用品、洗护、文具
- 购物：电商、服装、数码、化妆品、鞋包
- 娱乐：电影、游戏、KTV、旅游景点、会员订阅(视频/音乐)
- 学习：课程、书籍、考试、培训、论文、token/API
- 住房：房租、水电、物业、家电、家具
- 医疗健康：医院、药店、体检、健身
- 旅行：酒店、门票、旅行社
- 通讯：话费、流量、宽带
- 转账：人与人之间转账、还款、借钱（不确定用途的内部资金往来）
- 退款：商家退款、退货
- 投资：基金、股票、理财、证券
- 其他：实在无法判断的

【标签推荐要求】
同时为每笔交易推荐 1-3 个中文标签（不要从分类里选），用于用户后续筛选和统计。
标签用于标记：
- 资金归属：个人消费、公司报销、项目支出、家庭共用、朋友AA
- 消费场景：工作餐、约会、聚会、出差、网购、线下
- 其他用户可能用到的自定义维度

标签示例：工作餐、报销、网购、约会、出差、家庭、朋友聚会、通勤、学习投资、生活必需、冲动消费

【注意】
- 收入类交易必须从收入类中选
- 支出类交易必须从支出类中选
- 描述和商户名有参考价值，金额只作辅助
- 如果是人与人之间的转账（对方是人名），归类为"转账"
- 如果拿不准，选"其他"
- 标签要简洁，2-4个字，便于用户筛选

【待分类的交易（按商户分组）】
{json.dumps(batch_items, ensure_ascii=False, indent=2)}

请严格以 JSON 格式返回，不要任何其他文字：
{{
  "results": [
    {{"ref": "m0", "category": "分类名", "reason": "一句话判断依据", "tags": ["标签1", "标签2"]}}
  ]
}}"""

                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        response_format={"type": "json_object"},
                    )
                    text = resp.choices[0].message.content or ""
                    try:
                        result = json.loads(text)
                        for r in result.get("results", []):
                            ref = r.get("ref", "")
                            idx = int(ref[1:]) if ref.startswith("m") else -1
                            if 0 <= idx < len(batch):
                                ms = batch[idx]
                                cat = r.get("category", "")
                                reason = r.get("reason", "")
                                tags = r.get("tags", []) or []
                                # 把这个商户的所有交易都附上建议
                                for tid in ms["ids"]:
                                    suggestions[tid] = {"category": cat, "reason": reason, "tags": tags}
                    except (json.JSONDecodeError, ValueError, IndexError):
                        pass  # 某一批失败不影响其他批

            except Exception as e:
                # AI 调用失败，返回空建议（留着规则匹配兜底）
                pass

    # 为 pending 中的每条交易附加建议
    result_suggestions = []
    for t in pending:
        s = suggestions.get(t.id, {})
        m = (t.merchant or "").strip()
        # 尝试用规则匹配兜底
        if not s:
            rule_cat = db.match_rule(m) if m else None
            if rule_cat:
                s = {"category": rule_cat, "reason": "已有规则匹配"}
        result_suggestions.append({
            "id": t.id,
            "merchant": t.merchant or "",
            "description": t.description or "",
            "amount": t.amount,
            "direction": t.direction,
            "datetime": t.datetime,
            "source": t.source,
            "suggested_category": s.get("category", ""),
            "reason": s.get("reason", ""),
            "suggested_tags": s.get("tags", []),
            "is_fixed_candidate": any(fc["sample_id"] == t.id for fc in fixed_candidates),
        })

    global _ai_suggest_cache
    result_data = {
        "suggestions": result_suggestions,
        "fixed_candidates": fixed_candidates,
    }
    _ai_suggest_cache = result_data
    return jsonify(result_data)


@app.route("/api/pending/suggest-cache", methods=["GET"])
def api_pending_suggest_cache():
    """获取缓存的 AI 建议（跨页面保持）"""
    global _ai_suggest_cache
    if _ai_suggest_cache is None:
        return jsonify({"suggestions": [], "fixed_candidates": []})
    return jsonify(_ai_suggest_cache)


@app.route("/api/pending/convert-fixed", methods=["POST"])
def api_convert_to_fixed():
    """将交易标记为固定支出并添加至固定收支表"""
    data = request.get_json(force=True)
    merchant = data.get("merchant", "").strip()
    amount = data.get("amount")
    day = data.get("day_of_month", 1)
    direction = data.get("direction", "expense")
    if not merchant or not amount:
        return jsonify({"error": "缺少 merchant 或 amount"}), 400

    try:
        # 查找同一商户的所有交易，确定起止月份
        txns = db.query_transactions_db(merchant=merchant, per_page=9999, exact_merchant=True)
        items = txns[0] if txns else []
        months = sorted(set(t["datetime"][:7] for t in items if t.get("datetime")))
        start_month = months[0] if months else None
        end_month = months[-1] if len(months) > 1 else (start_month if start_month else None)

        # 添加到固定收支
        item_type = "expense" if direction == "expense" else "income"
        name = merchant
        cat = data.get("category", "其他")

        # 确认相关的交易分类
        txn_ids = data.get("txn_ids", [])
        if not txn_ids:
            all_txns = db.query_transactions_db(merchant=merchant, per_page=9999, exact_merchant=True)
            txn_ids = [t["id"] for t in all_txns[0]] if all_txns else []

        confirmed_category = data.get("category", "其他")
        for tid in txn_ids:
            db.set_classification(tid, confirmed_category)

        # 创建固定收支项
        item_id = db.add_fixed_item(
            type=item_type, name=name, amount=amount,
            day_of_month=day, category=cat,
            start_month=start_month, end_month=end_month,
        )
        return jsonify({"success": True, "fixed_item_id": item_id, "start_month": start_month, "end_month": end_month, "classified_count": len(txn_ids)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
# 用户设置 API
# ══════════════════════════════════════════════════════

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    settings = {
        "current_balance": db.get_setting("current_balance", "0"),
        "name": db.get_setting("name", ""),
        "safety_balance": db.get_setting("safety_balance", "0"),
        "api_key": db.get_setting("api_key", ""),
        "api_base_url": db.get_setting("api_base_url", "https://api.deepseek.com/v1"),
        "api_model": db.get_setting("api_model", "deepseek-chat"),
    }
    return jsonify(settings)


@app.route("/api/settings", methods=["PUT"])
def api_update_settings():
    data = request.get_json(force=True)
    for key in ("current_balance", "name", "safety_balance", "api_key", "api_base_url", "api_model"):
        if key in data:
            db.set_setting(key, str(data[key]))
    return jsonify({"success": True})


@app.route("/api/classifications/reset", methods=["POST"])
def api_reset_classifications():
    """删除所有交易分类记录，恢复到未分类状态"""
    try:
        db.reset_all_classifications()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
# 账户 API
# ══════════════════════════════════════════════════════

@app.route("/api/accounts", methods=["GET"])
def api_get_accounts():
    try:
        return jsonify(db.get_accounts())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/accounts", methods=["POST"])
def api_add_account():
    data = request.get_json(force=True)
    name = data.get("name")
    acct_type = data.get("type")
    balance = data.get("balance", 0.0)
    if not name or not acct_type:
        return jsonify({"error": "缺少 name 或 type 字段"}), 400
    try:
        acct_id = db.add_account(name, acct_type, balance)
        return jsonify({"success": True, "id": acct_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/accounts/<int:account_id>", methods=["PUT"])
def api_update_account(account_id):
    data = request.get_json(force=True)
    try:
        db.update_account(account_id, name=data.get("name"), type=data.get("type"), balance=data.get("balance"))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def api_delete_account(account_id):
    db.delete_account(account_id)
    return jsonify({"success": True})


@app.route("/api/accounts/recalc", methods=["POST"])
def api_recalc_accounts():
    """从交易数据一键重算所有账户余额"""
    try:
        result = db.recalc_accounts_from_transactions()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
# 固定收支 API
# ══════════════════════════════════════════════════════

@app.route("/api/fixed-items", methods=["GET"])
def api_get_fixed_items():
    item_type = request.args.get("type")
    try:
        return jsonify(db.get_fixed_items(type=item_type))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fixed-items", methods=["POST"])
def api_add_fixed_item():
    data = request.get_json(force=True)
    item_type = data.get("type")
    name = data.get("name")
    amount = data.get("amount")
    if not item_type or not name or amount is None:
        return jsonify({"error": "缺少 type、name 或 amount 字段"}), 400
    try:
        item_id = db.add_fixed_item(type=item_type, name=name, amount=amount,
                                    day_of_month=data.get("day_of_month"),
                                    category=data.get("category", ""), note=data.get("note", ""),
                                    start_month=data.get("start_month"), end_month=data.get("end_month"))
        return jsonify({"success": True, "id": item_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fixed-items/<int:item_id>", methods=["DELETE"])
def api_delete_fixed_item(item_id):
    try:
        db.delete_fixed_item(item_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
# 随礼管理 API（人情账本）
# ══════════════════════════════════════════════════════

@app.route("/api/gifts", methods=["GET"])
def api_get_gifts():
    status = request.args.get("status")  # paid / planned / None
    try:
        return jsonify(db.get_gift_events(status=status))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/gifts/summary", methods=["GET"])
def api_get_gifts_summary():
    try:
        return jsonify(db.get_gift_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/gifts", methods=["POST"])
def api_add_gift():
    data = request.get_json(force=True)
    person = (data.get("person") or "").strip()
    event_type = (data.get("event_type") or "").strip()
    amount = data.get("amount", 0)
    event_date = (data.get("event_date") or "").strip()
    if not person or not event_type or not event_date:
        return jsonify({"error": "对象、事件类型、日期必填"}), 400
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return jsonify({"error": "金额格式错误"}), 400
    try:
        gift_id = db.add_gift_event(
            person=person, event_type=event_type, amount=amount,
            event_date=event_date, travel_cost=data.get("travel_cost", 0),
            note=data.get("note", ""),
        )
        return jsonify({"success": True, "id": gift_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/gifts/<int:gift_id>", methods=["PUT"])
def api_update_gift(gift_id):
    data = request.get_json(force=True)
    try:
        db.update_gift_event(gift_id, **{k: v for k, v in data.items()
            if k in {"person", "event_type", "amount", "travel_cost", "event_date", "note"}})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/gifts/<int:gift_id>", methods=["DELETE"])
def api_delete_gift(gift_id):
    try:
        db.delete_gift_event(gift_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
# 层级分类 API
# ══════════════════════════════════════════════════════

@app.route("/api/category-options", methods=["GET"])
def api_get_categories():
    return jsonify(db.get_categories())


@app.route("/api/categories", methods=["POST"])
def api_add_category():
    data = request.get_json(force=True)
    name = data.get("name")
    parent_id = data.get("parent_id")
    if not name:
        return jsonify({"error": "缺少 name 字段"}), 400
    try:
        return jsonify({"success": True, "id": db.add_category(name, parent_id)})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/categories/<int:category_id>", methods=["DELETE"])
def api_delete_category(category_id):
    db.delete_category(category_id)
    return jsonify({"success": True})


# ══════════════════════════════════════════════════════
# 分类规则 API
# ══════════════════════════════════════════════════════

@app.route("/api/category-rules", methods=["GET"])
def api_get_category_rules():
    try:
        return jsonify(db.get_category_rules())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/category-rules", methods=["POST"])
def api_add_category_rule():
    data = request.get_json(force=True)
    keyword = data.get("keyword")
    category = data.get("category")
    if not keyword or not category:
        return jsonify({"error": "缺少 keyword 或 category 字段"}), 400
    try:
        rule_id = db.add_rule(keyword, category)
        return jsonify({"success": True, "id": rule_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/category-rules/<int:rule_id>/toggle", methods=["POST"])
def api_toggle_category_rule(rule_id):
    """启用/禁用规则"""
    try:
        new_val = db.toggle_rule(rule_id)
        if new_val is None:
            return jsonify({"error": "规则不存在"}), 404
        return jsonify({"success": True, "is_active": bool(new_val)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/category-rules/<int:rule_id>", methods=["PUT"])
def api_update_category_rule(rule_id):
    """更新规则"""
    data = request.get_json(force=True)
    try:
        db.update_rule(rule_id, keyword=data.get("keyword"), category=data.get("category"), is_active=data.get("is_active"))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/category-rules/<int:rule_id>", methods=["DELETE"])
def api_delete_category_rule(rule_id):
    try:
        db.delete_rule(rule_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
# 数据导出 API
# ══════════════════════════════════════════════════════

@app.route("/api/export", methods=["GET"])
def api_export_transactions():
    """导出所有交易为 JSON"""
    items = db.get_all_transactions()
    result = []
    for item in items:
        result.append({
            "datetime": item["datetime"],
            "date": item["date"],
            "amount": item["amount"],
            "direction": item["direction"],
            "merchant": item["merchant"],
            "description": item["description"],
            "category": item["category"],
            "source": item["source"],
            "account": item["account"],
        })
    return jsonify(result)


# ══════════════════════════════════════════════════════
# AI 对话 API
# ══════════════════════════════════════════════════════

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """AI 问答接口"""
    data = request.get_json(force=True)
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "消息不能为空"}), 400

    agent = get_agent()
    if not agent:
        return jsonify({"error": "请先在配置文件中设置 DEEPSEEK_API_KEY"}), 400

    try:
        # 更新 agent 的交易数据（可能已有新分类）
        agent.transactions = effective_transactions()
        reply = agent.chat(message)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": f"AI 回复失败: {str(e)}"}), 500


@app.route("/api/chat/reset", methods=["POST"])
def api_chat_reset():
    """重置对话"""
    agent = get_agent()
    if agent:
        agent.reset()
    return jsonify({"success": True})


@app.route("/api/chat/status", methods=["GET"])
def api_chat_status():
    """检查 AI 是否可用"""
    agent = get_agent()
    return jsonify({
        "available": agent is not None,
        "has_api_key": bool(get_llm_config().get("api_key")),
    })


@app.route("/api/chat/history", methods=["GET"])
def api_chat_history():
    """获取对话历史"""
    agent = get_agent()
    if not agent:
        return jsonify({"history": []})
    return jsonify({"history": agent.get_history()})


# ══════════════════════════════════════════════════
# 未来流水情况表 API
# ══════════════════════════════════════════════════

@app.route("/api/cashflow-table")
def api_cashflow_table():
    """
    生成月度的流水情况表，类似用户 Excel 格式：
    - 收入来源（固定收入）、支出项目（固定支出+日常预算）
    - 每月一列，展示未来 12 个月
    - 包含期末余额行
    """
    try:
        from datetime import datetime as dt_mod, timedelta
        today = dt_mod.now()
        # 从当月开始展示 12 个月
        start_year = today.year
        start_month = today.month

        # 获取当前余额（从账户管理余额 - 债务）
        accounts_balance = db.get_accounts_balance()
        total_debt = db.get_total_debt()
        current_balance = accounts_balance - total_debt

        # 获取安全余额
        safety_str = db.get_setting("safety_balance", "0")
        try:
            safety_balance = float(safety_str)
        except (ValueError, TypeError):
            safety_balance = 0.0

        # 构建月份列表
        months = []
        for i in range(12):
            y = start_year + (start_month + i - 1) // 12
            m = (start_month + i - 1) % 12 + 1
            months.append(f"{y:04d}-{m:02d}")

        # 获取固定收入和支出（按月份过滤）
        def get_monthly_items(type_str):
            """对每个月获取有效的固定收支"""
            items = db.get_fixed_items(type_str)
            result = []
            for item in items:
                has_term = item.get("has_term")
                start_m = item.get("start_month")
                end_m = item.get("end_month")
                amt = float(item["amount"])
                cat = item.get("category", "其他")
                row_data = {"name": item["name"], "category": cat, "values": {}, "id": item["id"]}
                for ym in months:
                    # 有期限限制时判断是否在有效期内
                    if has_term:
                        if start_m and ym < start_m:
                            row_data["values"][ym] = 0
                            continue
                        if end_m and ym > end_m:
                            row_data["values"][ym] = 0
                            continue
                    row_data["values"][ym] = amt if type_str != "income" or (item.get("note") or "").find("confirmed:") >= 0 else amt
                result.append(row_data)
            return result

        income_rows = get_monthly_items("income")
        expense_rows = get_monthly_items("expense")

        # 日常支出（使用历史平均）
        txns_all = effective_transactions()
        from analyzer import get_spending_trend, project_cashflow
        trend = get_spending_trend(txns_all, num_months=3)
        avg_daily_expense = 0
        if trend:
            daily_total = sum(m["total_expense"] for m in trend) / len(trend)
            # 减去固定支出，得到可变的日常支出预算
            fixed_expense_total = sum(float(item["amount"]) for item in db.get_fixed_items("expense"))
            avg_daily_expense = max(0, daily_total - fixed_expense_total)

        if avg_daily_expense > 0:
            expense_rows.append({
                "name": "日常消费（预算）",
                "category": "日常",
                "is_budget": True,
                "values": {ym: round(avg_daily_expense, 2) for ym in months},
            })

        # 计算汇总
        total_income_row = {"name": "收入合计", "is_total": True, "values": {}}
        total_expense_row = {"name": "支出合计", "is_total": True, "values": {}}
        balance_row = {"name": "月末余额", "is_balance": True, "values": {}}
        net_row = {"name": "当月结余", "is_net": True, "values": {}}

        balance = current_balance
        for ym in months:
            total_inc = sum(r["values"].get(ym, 0) for r in income_rows)
            total_exp = sum(r["values"].get(ym, 0) for r in expense_rows)
            total_income_row["values"][ym] = round(total_inc, 2)
            total_expense_row["values"][ym] = round(total_exp, 2)
            net = total_inc - total_exp
            net_row["values"][ym] = round(net, 2)
            balance += net
            balance_row["values"][ym] = round(balance, 2)

        return jsonify({
            "months": months,
            "current_balance": current_balance,
            "accounts_balance": accounts_balance,
            "total_debt": total_debt,
            "safety_balance": safety_balance,
            "income_rows": income_rows,
            "expense_rows": expense_rows,
            "total_income": total_income_row,
            "total_expense": total_expense_row,
            "net": net_row,
            "balance": balance_row,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/cashflow")
def cashflow_page():
    """未来流水页面"""
    return render_template("cashflow.html")


@app.route("/monthly-analysis")
def monthly_analysis_page():
    """月度分析页面"""
    return render_template("monthly_analysis.html")


@app.route("/tags")
def tags_page():
    """标签统计页面"""
    return render_template("tags.html")


@app.route("/gifts")
def gifts_page():
    """随礼管理页面"""
    return render_template("gifts.html")


# ══════════════════════════════════════════════════
# 储蓄目标 API
# ══════════════════════════════════════════════════

@app.route("/api/savings-goals", methods=["GET"])
def api_get_savings_goals():
    try:
        return jsonify(db.get_savings_goals())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/savings-goals", methods=["POST"])
def api_add_savings_goal():
    data = request.get_json(force=True)
    name = data.get("name")
    if not name:
        return jsonify({"error": "缺少 name 字段"}), 400
    try:
        goal_id = db.add_savings_goal(
            name=name, target_amount=data.get("target_amount", 0),
            current_amount=data.get("current_amount", 0),
            target_date=data.get("target_date"),
            account_id=data.get("account_id"),
            monthly_save=data.get("monthly_save", 0),
            note=data.get("note", ""),
        )
        return jsonify({"success": True, "id": goal_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/savings-goals/<int:goal_id>", methods=["PUT"])
def api_update_savings_goal(goal_id):
    data = request.get_json(force=True)
    allowed = ("name","target_amount","current_amount","target_date","account_id","monthly_save","status","note")
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "没有需要更新的字段"}), 400
    try:
        db.update_savings_goal(goal_id, **updates)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/savings-goals/<int:goal_id>", methods=["DELETE"])
def api_delete_savings_goal(goal_id):
    try:
        db.delete_savings_goal(goal_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════
# 理财账户 API
# ══════════════════════════════════════════════════

@app.route("/api/investment-accounts", methods=["GET"])
def api_get_investment_accounts():
    try:
        return jsonify(db.get_investment_accounts())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/investment-accounts", methods=["POST"])
def api_add_investment_account():
    data = request.get_json(force=True)
    name = data.get("name")
    account_type = data.get("account_type")
    if not name or not account_type:
        return jsonify({"error": "缺少 name 或 account_type 字段"}), 400
    try:
        inv_id = db.add_investment_account(
            name=name, account_type=account_type,
            institution=data.get("institution", ""),
            principal=data.get("principal", 0),
            market_value=data.get("market_value", 0),
            valuation_date=data.get("valuation_date"),
            risk_level=data.get("risk_level", ""),
            note=data.get("note", ""),
        )
        return jsonify({"success": True, "id": inv_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/investment-accounts/<int:inv_id>", methods=["PUT"])
def api_update_investment_account(inv_id):
    data = request.get_json(force=True)
    allowed = ("name","institution","account_type","principal","market_value","valuation_date","risk_level","note","is_active")
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "没有需要更新的字段"}), 400
    try:
        db.update_investment_account(inv_id, **updates)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/investment-accounts/<int:inv_id>", methods=["DELETE"])
def api_delete_investment_account(inv_id):
    try:
        db.delete_investment_account(inv_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════
# 收入确认 API
# ══════════════════════════════════════════════════

@app.route("/api/income/confirm", methods=["POST"])
def api_confirm_income():
    data = request.get_json(force=True)
    item_id = data.get("item_id")
    if not item_id:
        return jsonify({"error": "缺少 item_id 字段"}), 400
    try:
        result = db.confirm_income(item_id, actual_amount=data.get("actual_amount"), actual_date=data.get("actual_date"))
        if result is None:
            return jsonify({"error": "收入项不存在或不是收入类型"}), 404
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/income/pending", methods=["GET"])
def api_get_pending_income():
    try:
        return jsonify(db.get_pending_income())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════
# 提醒 API
# ══════════════════════════════════════════════════

@app.route("/api/reminders", methods=["GET"])
def api_get_reminders():
    try:
        return jsonify(db.get_reminders())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════
# 数据备份与恢复 API
# ══════════════════════════════════════════════════

import shutil
import datetime as dt_lib

@app.route("/api/backup", methods=["POST"])
def api_create_backup():
    """创建数据备份"""
    try:
        backup_dir = Path(db.db_path).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt_lib.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"backup_{timestamp}.db"
        shutil.copy2(db.db_path, str(backup_path))
        return jsonify({"success": True, "path": str(backup_path), "timestamp": timestamp})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/backup/list", methods=["GET"])
def api_list_backups():
    """列出所有备份文件"""
    try:
        backup_dir = Path(db.db_path).parent / "backups"
        if not backup_dir.exists():
            return jsonify([])
        files = sorted(backup_dir.glob("backup_*.db"), reverse=True)
        result = []
        for f in files:
            size = f.stat().st_size
            mtime = dt_lib.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            result.append({"path": str(f.name), "size": size, "mtime": mtime})
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/backup/restore", methods=["POST"])
def api_restore_backup():
    """从备份文件恢复"""
    data = request.get_json(force=True)
    backup_name = data.get("backup")
    if not backup_name:
        return jsonify({"error": "缺少 backup 参数"}), 400
    try:
        backup_dir = Path(db.db_path).parent / "backups"
        backup_path = backup_dir / backup_name
        if not backup_path.exists():
            return jsonify({"error": "备份文件不存在"}), 404
        shutil.copy2(str(backup_path), db.db_path)
        return jsonify({"success": True, "message": "恢复成功，请刷新页面"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/export/csv", methods=["GET"])
def api_export_csv():
    """导出交易为 CSV"""
    import csv
    import io
    items = db.get_all_transactions()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["时间", "日期", "金额", "方向", "商户", "描述", "分类", "来源", "账户"])
    for item in items:
        writer.writerow([
            item["datetime"], item["date"], item["amount"],
            {"income":"收入","expense":"支出","neutral":"中性"}.get(item["direction"],item["direction"]),
            item["merchant"], item["description"], item["category"],
            item["source"], item["account"],
        ])
    csv_content = output.getvalue()
    output.close()
    from flask import Response
    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


# ══════════════════════════════════════════════════
# 债务管理 API
# ══════════════════════════════════════════════════

@app.route("/api/debts", methods=["GET"])
def api_get_debts():
    try:
        return jsonify(db.get_debts())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/debts", methods=["POST"])
def api_add_debt():
    data = request.get_json(force=True)
    total = data.get("total_amount")
    if not total or float(total) <= 0:
        return jsonify({"error": "请输入有效的负债总金额"}), 400
    try:
        debt_id = db.add_debt(
            total_amount=float(total),
            remaining_amount=data.get("remaining_amount"),
            account_id=data.get("account_id"),
            reason=data.get("reason", ""),
            interest_rate=data.get("interest_rate", 0),
            interest_type=data.get("interest_type", "simple"),
            repayment_plan=data.get("repayment_plan", ""),
            due_date=data.get("due_date"),
            creditor=data.get("creditor", ""),
        )
        return jsonify({"success": True, "id": debt_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/debts/<int:debt_id>", methods=["PUT"])
def api_update_debt(debt_id):
    data = request.get_json(force=True)
    allowed = ("account_id","reason","total_amount","remaining_amount","interest_rate","interest_type","repayment_plan","due_date","creditor","status","is_active")
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "没有需要更新的字段"}), 400
    try:
        db.update_debt(debt_id, **updates)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/debts/<int:debt_id>", methods=["DELETE"])
def api_delete_debt(debt_id):
    try:
        db.delete_debt(debt_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/debts/<int:debt_id>/repay", methods=["POST"])
def api_repay_debt(debt_id):
    data = request.get_json(force=True)
    amount = data.get("amount")
    if not amount or float(amount) <= 0:
        return jsonify({"error": "请输入有效还款金额"}), 400
    try:
        result = db.repay_debt(debt_id, float(amount), data.get("repay_date"))
        if result is None:
            return jsonify({"error": "债务不存在"}), 404
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════
# 固定收支期限管理 & 分期付款 API
# ══════════════════════════════════════════════════

@app.route("/api/fixed-items/<int:item_id>/term", methods=["GET"])
def api_get_fixed_item_term(item_id):
    try:
        term = db.get_fixed_item_term(item_id)
        if term:
            payments = db.get_installment_payments(term["id"])
            term["payments"] = payments
        return jsonify(term or {})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/fixed-items/<int:item_id>/term", methods=["PUT"])
def api_set_fixed_item_term(item_id):
    data = request.get_json(force=True)
    try:
        has_term = data.get("has_term", False)
        if not has_term:
            # 删除已有期限配置
            term = db.get_fixed_item_term(item_id)
            if term:
                db.update_fixed_item_term(term["id"], has_term=0, status="deleted")
            return jsonify({"success": True})

        db.add_fixed_item_term(
            fixed_item_id=item_id,
            has_term=1,
            total_periods=data.get("total_periods", 0),
            paid_periods=data.get("paid_periods", 0),
            start_month=data.get("start_month"),
            end_month=data.get("end_month"),
            period_amount=data.get("period_amount"),
            is_installment=data.get("is_installment", 0),
            auto_stop=data.get("auto_stop", 0),
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/installments/<int:payment_id>/pay", methods=["POST"])
def api_pay_installment(payment_id):
    data = request.get_json(force=True) or {}
    try:
        result = db.mark_installment_paid(payment_id, data.get("paid_date"))
        if result is None:
            return jsonify({"error": "分期记录不存在"}), 404
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/installments", methods=["GET"])
def api_get_installments():
    term_id = request.args.get("term_id", type=int)
    try:
        return jsonify(db.get_installment_payments(term_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/fixed-items/<int:item_id>/term", methods=["DELETE"])
def api_delete_fixed_item_term(item_id):
    try:
        term = db.get_fixed_item_term(item_id)
        if term:
            db.update_fixed_item_term(term["id"], has_term=0, status="deleted")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 启动
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
