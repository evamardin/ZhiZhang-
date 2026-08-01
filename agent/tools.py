"""工具定义层

为 LLM 提供可调用的工具（function calling）。
每个工具对应 analyzer.py 中的一个分析函数。
"""
import json
from analyzer import (
    get_monthly_summary,
    get_category_breakdown,
    query_transactions,
    get_spending_trend,
    get_top_merchants,
    project_cashflow,
    get_pending_categories,
)
from data_loader import Transaction

# ── 工具 Schema（OpenAI function calling 格式）────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_monthly_summary",
            "description": "获取指定月份的收支汇总，包括总收入、总支出、净结余、笔数、日均支出和最大单笔支出。当用户问'这个月花了多少''收入多少'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "year_month": {
                        "type": "string",
                        "description": "月份，格式 YYYY-MM，如 2026-07。如果用户说'这个月'，用当前月份。",
                    }
                },
                "required": ["year_month"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_category_breakdown",
            "description": "获取指定月份的分类支出明细，包括每个分类的金额、笔数和占比。当用户问'钱花在哪了''哪类支出最多'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "year_month": {
                        "type": "string",
                        "description": "月份，格式 YYYY-MM，如 2026-07。",
                    }
                },
                "required": ["year_month"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_transactions",
            "description": "按条件查询交易记录。支持按日期范围、分类、商户名、收支方向、金额范围筛选。当用户问'在星巴克花了多少''最近的餐饮消费'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "开始日期，格式 YYYY-MM-DD 或 YYYY-MM"},
                    "end_date": {"type": "string", "description": "结束日期，格式 YYYY-MM-DD 或 YYYY-MM"},
                    "category": {"type": "string", "description": "分类名，如 餐饮/交通/日用百货 等"},
                    "merchant": {"type": "string", "description": "商户名关键词，模糊匹配"},
                    "direction": {"type": "string", "enum": ["income", "expense", "neutral"], "description": "收支方向"},
                    "min_amount": {"type": "number", "description": "最小金额"},
                    "max_amount": {"type": "number", "description": "最大金额"},
                    "limit": {"type": "integer", "description": "返回条数上限，默认20", "default": 20},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spending_trend",
            "description": "获取最近几个月的消费趋势，包括每月的总支出、总收入和净结余。当用户问'最近几个月支出趋势''消费变化'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "num_months": {
                        "type": "integer",
                        "description": "查看最近几个月的数据，默认6",
                        "default": 6,
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_merchants",
            "description": "获取消费金额最高的商户排行。当用户问'钱都花在哪了''消费最多的商户'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "year_month": {
                        "type": "string",
                        "description": "指定月份 YYYY-MM，不传则统计全部",
                    },
                    "limit": {"type": "integer", "description": "返回条数，默认10", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_cashflow",
            "description": "基于历史数据预测未来几个月的现金流和余额变化。当用户问'下个月还剩多少''未来余额够不够'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "current_balance": {
                        "type": "number",
                        "description": "当前账户余额，如果不传则使用配置中的默认值",
                    },
                    "months": {
                        "type": "integer",
                        "description": "预测未来几个月，默认3",
                        "default": 3,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pending_categories",
            "description": "获取所有待分类的交易记录，这些交易需要用户确认分类。当用户想处理待分类交易时使用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # ── 写工具 ──
    {
        "type": "function",
        "function": {
            "name": "batch_classify_merchant",
            "description": "将某个商户的所有交易设置为指定分类，并添加分类规则。当用户说'把星巴克设为餐饮''把所有 lucky coffee 设为餐饮'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "merchant": {"type": "string", "description": "商户名称，如 '星巴克'、'Lucky Coffee'"},
                    "category": {"type": "string", "description": "分类名称，如 '餐饮'，可用 '餐饮/咖啡' 格式创建子分类"},
                },
                "required": ["merchant", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "batch_tag_merchant",
            "description": "给某个商户的所有交易添加指定标签（如果标签不存在则自动创建）。当用户说'给星巴克加个饮料标签''给 lucky coffee 加一个饮料的标签'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "merchant": {"type": "string", "description": "商户名称，如 '星巴克'"},
                    "tag_name": {"type": "string", "description": "标签名称，如 '饮料'、'咖啡'"},
                },
                "required": ["merchant", "tag_name"],
            },
        },
    },
]


def execute_tool(name: str, args: dict, transactions: list[Transaction], db=None) -> str:
    """执行工具调用，返回 JSON 字符串结果"""
    try:
        if name == "get_monthly_summary":
            result = get_monthly_summary(
                transactions,
                args.get("year_month", ""),
                start_date=args.get("start_date"),
                end_date=args.get("end_date"),
            )
        elif name == "get_category_breakdown":
            result = get_category_breakdown(
                transactions,
                args.get("year_month", ""),
                start_date=args.get("start_date"),
                end_date=args.get("end_date"),
            )
        elif name == "query_transactions":
            result = query_transactions(transactions, **args)
            # Transaction 对象转为可序列化的 dict
            result = [
                {
                    "datetime": t.datetime,
                    "amount": t.amount,
                    "direction": t.direction,
                    "merchant": t.merchant,
                    "description": t.description,
                    "category": t.category,
                    "source": t.source,
                }
                for t in result
            ]
        elif name == "get_spending_trend":
            result = get_spending_trend(
                transactions,
                args.get("num_months", 6),
                start_date=args.get("start_date"),
                end_date=args.get("end_date"),
            )
        elif name == "get_top_merchants":
            result = get_top_merchants(
                transactions,
                year_month=args.get("year_month"),
                start_date=args.get("start_date"),
                end_date=args.get("end_date"),
                limit=args.get("limit", 10),
            )
        elif name == "project_cashflow":
            result = project_cashflow(
                transactions,
                current_balance=args.get("current_balance", 5000),
                months=args.get("months", 3),
            )
        elif name == "get_pending_categories":
            result = get_pending_categories(transactions)
        elif name == "batch_classify_merchant":
            result = _batch_classify_merchant(db, args)
        elif name == "batch_tag_merchant":
            result = _batch_tag_merchant(db, args)
        else:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)

        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _batch_classify_merchant(db, args):
    """批量给商户交易设分类（写操作）"""
    if db is None:
        return {"error": "数据库不可用，无法执行写入操作"}
    merchant = (args.get("merchant") or "").strip()
    category = (args.get("category") or "").strip()
    if not merchant or not category:
        return {"error": "merchant 和 category 必填"}
    items, _ = db.query_transactions_db(merchant=merchant, per_page=99999, exact_merchant=True)
    count = 0
    for item in items:
        db.set_classification(item["id"], category)
        count += 1
    db.upsert_rule(merchant, category)
    # 知识沉淀：把新商户写回本体实例（越用越准闭环）
    try:
        from knowledge.query import upsert_merchant
        upsert_merchant(merchant, category)
    except Exception:
        pass
    # 自动创建父子分类
    if "/" in category:
        parts = category.split("/", 1)
        parent_name, child_name = parts[0].strip(), parts[1].strip()
        if parent_name and child_name:
            parent_id = None
            for c in db.get_categories():
                if c["name"] == parent_name and not c["parent_id"]:
                    parent_id = c["id"]
                    break
            if not parent_id:
                parent_id = db.add_category(parent_name)
            exists = any(c["name"] == child_name and c["parent_id"] == parent_id for c in db.get_categories())
            if not exists:
                db.add_category(child_name, parent_id)
    return {"success": True, "count": count, "merchant": merchant, "category": category}


def _batch_tag_merchant(db, args):
    """批量给商户交易加标签（写操作）"""
    if db is None:
        return {"error": "数据库不可用，无法执行写入操作"}
    merchant = (args.get("merchant") or "").strip()
    tag_name = (args.get("tag_name") or "").strip()
    if not merchant or not tag_name:
        return {"error": "merchant 和 tag_name 必填"}
    # 查找该商户所有交易
    items, _ = db.query_transactions_db(merchant=merchant, per_page=99999, exact_merchant=True)
    # 确保标签存在
    tag = db.add_tag(tag_name)
    tag_id = tag.get("id") if isinstance(tag, dict) else None
    count = 0
    for item in items:
        try:
            db.add_transaction_tag(item["id"], tag_name=tag_name, tag_id=tag_id)
            count += 1
        except Exception:
            pass  # 可能已存在该标签
    return {"success": True, "count": count, "merchant": merchant, "tag": tag_name}
