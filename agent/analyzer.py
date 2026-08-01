"""财务分析层

基于 data_loader.py 中的 Transaction 数据结构进行收支统计与趋势分析。
所有函数接收 transactions 列表，内部会过滤掉 is_duplicate=True 的记录。
"""
from collections import Counter, defaultdict
from datetime import datetime


def _filter_valid(transactions):
    """过滤掉 is_duplicate=True 的重复记录"""
    return [t for t in transactions if not t.is_duplicate]


def _days_in_month(year_month):
    """计算某月的天数，year_month 格式 YYYY-MM"""
    try:
        y, m = map(int, year_month.split("-"))
        next_month = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
        return (next_month - datetime(y, m, 1)).days
    except (ValueError, AttributeError):
        return 0


def _shift_month(year_month, n):
    """月份偏移 n 个月（n 可正可负），返回 YYYY-MM"""
    y, m = map(int, year_month.split("-"))
    total = y * 12 + (m - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _date_ge(date_str, ref):
    """判断 date_str >= ref，ref 支持 YYYY-MM 或 YYYY-MM-DD"""
    return (date_str[:7] if len(ref) == 7 else date_str) >= ref


def _date_le(date_str, ref):
    """判断 date_str <= ref，ref 支持 YYYY-MM 或 YYYY-MM-DD"""
    return (date_str[:7] if len(ref) == 7 else date_str) <= ref


def _days_in_range(start_date, end_date):
    """计算日期范围的天数（含首尾），start_date/end_date 格式 YYYY-MM-DD"""
    try:
        from datetime import datetime as _dt
        d1 = _dt.strptime(start_date, "%Y-%m-%d")
        d2 = _dt.strptime(end_date, "%Y-%m-%d")
        return (d2 - d1).days + 1
    except (ValueError, TypeError):
        return 0


def _filter_by_range(txns, start_date=None, end_date=None):
    """按日期范围过滤交易（start_date/end_date 可为空）"""
    result = txns
    if start_date:
        result = [t for t in result if _date_ge(t.date, start_date)]
    if end_date:
        result = [t for t in result if _date_le(t.date, end_date)]
    return result


def get_monthly_summary(transactions, year_month="", start_date=None, end_date=None):
    """返回指定月份或日期范围的收支汇总"""
    txns = _filter_valid(transactions)
    if year_month:
        month_txns = [t for t in txns if t.year_month == year_month]
    else:
        month_txns = _filter_by_range(txns, start_date, end_date)

    incomes = [t for t in month_txns if t.direction == "income"]
    expenses = [t for t in month_txns if t.direction == "expense"]

    total_income = sum(t.amount for t in incomes)
    total_expense = sum(t.amount for t in expenses)

    if year_month:
        days = _days_in_month(year_month)
    elif start_date and end_date:
        days = _days_in_range(start_date, end_date)
    else:
        # 全量数据：用实际日期跨度兜底
        dates = sorted({t.date for t in month_txns if t.date})
        days = _days_in_range(dates[0], dates[-1]) if len(dates) >= 2 else (1 if dates else 0)
    avg_daily = total_expense / days if days else 0.0

    largest = {"amount": 0.0, "merchant": "", "category": ""}
    if expenses:
        top = max(expenses, key=lambda t: t.amount)
        largest = {"amount": top.amount, "merchant": top.merchant, "category": top.category}

    return {
        "year_month": year_month,
        "start_date": start_date,
        "end_date": end_date,
        "total_income": round(total_income, 2),
        "total_expense": round(total_expense, 2),
        "net": round(total_income - total_expense, 2),
        "income_count": len(incomes),
        "expense_count": len(expenses),
        "avg_daily_expense": round(avg_daily, 2),
        "largest_expense": largest,
    }


def get_category_breakdown(transactions, year_month="", start_date=None, end_date=None):
    """返回指定月份或日期范围的分类支出明细，按金额降序。只统计 expense 方向"""
    txns = _filter_valid(transactions)
    if year_month:
        expenses = [t for t in txns if t.year_month == year_month and t.direction == "expense"]
    else:
        expenses = [t for t in txns if t.direction == "expense"]
        expenses = _filter_by_range(expenses, start_date, end_date)

    total = sum(t.amount for t in expenses)
    cat_map = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for t in expenses:
        cat_map[t.category]["amount"] += t.amount
        cat_map[t.category]["count"] += 1

    result = []
    for cat, data in cat_map.items():
        pct = (data["amount"] / total * 100) if total else 0.0
        result.append({
            "category": cat,
            "amount": round(data["amount"], 2),
            "count": data["count"],
            "percentage": round(pct, 1),
        })

    result.sort(key=lambda x: x["amount"], reverse=True)
    return result


def query_transactions(transactions, start_date=None, end_date=None, category=None,
                      merchant=None, direction=None, min_amount=None, max_amount=None,
                      limit=20):
    """按条件查询交易记录，返回匹配的 Transaction 列表（最多 limit 条）"""
    txns = _filter_valid(transactions)
    results = []

    for t in txns:
        if start_date and not _date_ge(t.date, start_date):
            continue
        if end_date and not _date_le(t.date, end_date):
            continue
        if category and t.category != category:
            continue
        if merchant and merchant not in t.merchant:
            continue
        if direction and t.direction != direction:
            continue
        if min_amount is not None and t.amount < min_amount:
            continue
        if max_amount is not None and t.amount > max_amount:
            continue
        results.append(t)

    return results[:limit]


def get_spending_trend(transactions, num_months=6, start_date=None, end_date=None):
    """返回最近 num_months 个月的消费趋势，按时间正序。只统计 expense 和 income"""
    txns = _filter_valid(transactions)
    if start_date or end_date:
        txns = _filter_by_range(txns, start_date, end_date)
    months = sorted({t.year_month for t in txns if t.year_month})
    if not months:
        return []

    recent = months[-num_months:] if len(months) >= num_months else months

    result = []
    for ym in recent:
        month_txns = [t for t in txns if t.year_month == ym]
        total_expense = sum(t.amount for t in month_txns if t.direction == "expense")
        total_income = sum(t.amount for t in month_txns if t.direction == "income")
        result.append({
            "year_month": ym,
            "total_expense": round(total_expense, 2),
            "total_income": round(total_income, 2),
            "net": round(total_income - total_expense, 2),
        })
    return result


def get_top_merchants(transactions, year_month=None, start_date=None, end_date=None, limit=10):
    """返回消费商户排行，按总金额降序。只统计 expense 方向
    统计前先按本体归一化商户名（门店变体 → 品牌实体），合并分裂商户
    """
    txns = _filter_valid(transactions)
    expenses = [t for t in txns if t.direction == "expense"]
    if year_month:
        expenses = [t for t in expenses if t.year_month == year_month]
    expenses = _filter_by_range(expenses, start_date, end_date)

    try:
        from knowledge.query import get_ontology
        ontology = get_ontology()
    except Exception:
        ontology = None

    merchant_map = defaultdict(lambda: {"total_amount": 0.0, "count": 0, "categories": Counter()})
    for t in expenses:
        name = ontology.normalized_name(t.merchant) if ontology else t.merchant
        m = merchant_map[name]
        m["total_amount"] += t.amount
        m["count"] += 1
        m["categories"][t.category] += 1

    result = []
    for merchant, data in merchant_map.items():
        top_cat = data["categories"].most_common(1)[0][0] if data["categories"] else ""
        result.append({
            "merchant": merchant,
            "total_amount": round(data["total_amount"], 2),
            "count": data["count"],
            "category": top_cat,
        })

    result.sort(key=lambda x: x["total_amount"], reverse=True)
    return result[:limit]


def project_cashflow(transactions, current_balance=5000, months=3):
    """基于近几个月历史数据预测未来现金流"""
    txns = _filter_valid(transactions)
    trend = get_spending_trend(txns, num_months=months)

    if not trend:
        return {
            "current_balance": float(current_balance),
            "avg_monthly_income": 0.0,
            "avg_monthly_expense": 0.0,
            "projections": [],
        }

    avg_income = sum(m["total_income"] for m in trend) / len(trend)
    avg_expense = sum(m["total_expense"] for m in trend) / len(trend)

    # 预测起始月份：最近一个数据月份的下一个月
    start_ym = _shift_month(trend[-1]["year_month"], 1)

    projections = []
    balance = float(current_balance)
    for i in range(months):
        ym = _shift_month(start_ym, i)
        end_bal = balance + avg_income - avg_expense
        projections.append({
            "month": ym,
            "start_balance": round(balance, 2),
            "projected_income": round(avg_income, 2),
            "projected_expense": round(avg_expense, 2),
            "end_balance": round(end_bal, 2),
        })
        balance = end_bal

    return {
        "current_balance": float(current_balance),
        "avg_monthly_income": round(avg_income, 2),
        "avg_monthly_expense": round(avg_expense, 2),
        "projections": projections,
    }


def get_pending_categories(transactions):
    """返回所有待分类交易（category == "待分类"），按金额降序，最多 20 条"""
    txns = _filter_valid(transactions)
    pending = [t for t in txns if t.category == "待分类"]
    pending.sort(key=lambda t: t.amount, reverse=True)
    return [
        {
            "merchant": t.merchant,
            "description": t.description,
            "amount": round(t.amount, 2),
            "datetime": t.datetime,
            "source": t.source,
        }
        for t in pending[:20]
    ]


if __name__ == "__main__":
    # 简单测试：构造 mock Transaction 数据
    from dataclasses import dataclass

    @dataclass
    class MockTxn:
        id: str
        datetime: str
        date: str
        year_month: str
        amount: float
        direction: str
        merchant: str
        description: str
        category: str
        category_confidence: str
        source: str
        account: str
        status: str
        transaction_id: str
        is_duplicate: bool

    sample = [
        MockTxn("1", "2026-07-01 10:00:00", "2026-07-01", "2026-07", 50.0, "expense",
                "星巴克", "拿铁", "餐饮", "high", "wechat", "招行", "done", "t1", False),
        MockTxn("2", "2026-07-02 09:00:00", "2026-07-02", "2026-07", 5.0, "expense",
                "成都地铁", "地铁", "交通", "high", "alipay", "支付宝", "done", "t2", False),
        MockTxn("3", "2026-07-05 12:00:00", "2026-07-05", "2026-07", 8000.0, "income",
                "公司", "工资", "工资", "high", "bank", "招行", "done", "t3", False),
        MockTxn("4", "2026-07-10 18:00:00", "2026-07-10", "2026-07", 120.0, "expense",
                "星巴克", "咖啡", "餐饮", "high", "wechat", "招行", "done", "t4", False),
        MockTxn("5", "2026-06-15 14:00:00", "2026-06-15", "2026-06", 4500.0, "expense",
                "超市", "采购", "日用百货", "medium", "wechat", "招行", "done", "t5", False),
        MockTxn("6", "2026-07-12 20:00:00", "2026-07-12", "2026-07", 88.0, "expense",
                "未知商户", "??", "待分类", "pending", "wechat", "招行", "done", "t6", False),
        MockTxn("7", "2026-07-01 10:05:00", "2026-07-01", "2026-07", 50.0, "expense",
                "星巴克", "拿铁", "餐饮", "high", "bank", "招行", "done", "t7", True),  # 重复记录
    ]

    print("=== 月度汇总 ===")
    print(get_monthly_summary(sample, "2026-07"))
    print("\n=== 分类明细 ===")
    print(get_category_breakdown(sample, "2026-07"))
    print("\n=== 查询交易（餐饮）===")
    print([t.merchant for t in query_transactions(sample, category="餐饮")])
    print("\n=== 查询交易（金额>=100）===")
    print([t.merchant for t in query_transactions(sample, min_amount=100)])
    print("\n=== 消费趋势 ===")
    print(get_spending_trend(sample, num_months=3))
    print("\n=== 商户排行（2026-07）===")
    print(get_top_merchants(sample, year_month="2026-07"))
    print("\n=== 现金流预测 ===")
    print(project_cashflow(sample, current_balance=5000, months=3))
    print("\n=== 待分类交易 ===")
    print(get_pending_categories(sample))
