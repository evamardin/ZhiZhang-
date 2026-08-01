"""数据加载层

加载微信/支付宝/银行三份 TSV 交易明细，统一格式，去重，自动分类。
"""
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── 统一交易模型 ──────────────────────────────────────

@dataclass
class Transaction:
    """统一交易记录格式"""
    id: str = ""
    datetime: str = ""          # "2026-07-25 13:15:15"
    date: str = ""              # "2026-07-25"
    year_month: str = ""        # "2026-07"
    amount: float = 0.0         # 绝对金额
    direction: str = ""         # "income" / "expense" / "neutral"
    merchant: str = ""          # 商户/对方
    description: str = ""       # 描述
    category: str = ""          # 分类
    category_confidence: str = ""  # "high" / "medium" / "pending"
    source: str = ""            # "wechat" / "alipay" / "bank"
    account: str = ""           # 支付方式/账户
    status: str = ""            # 交易状态
    transaction_id: str = ""   # 原始交易号
    is_duplicate: bool = False  # 银行侧重复记录


# ── 分类规则 ──────────────────────────────────────────

# 支付宝自带分类 -> 统一分类
ALIPAY_CATEGORY_MAP = {
    "餐饮美食": "餐饮",
    "交通出行": "交通",
    "日用百货": "日用百货",
    "退款": "退款",
    "服饰装扮": "购物",
    "文化休闲": "娱乐",
    "充值缴费": "通讯",
    "美容美发": "医疗健康",
    "医疗健康": "医疗健康",
    "教育培训": "学习",
    "房产": "住房",
    "酒店旅游": "旅行",
    "数码电器": "购物",
    "母婴亲子": "购物",
    "运动健身": "娱乐",
    "公益": "社交",
    "商业服务": "其他",
    "生活服务": "其他",
    "充值": "转账",
}

# 商户名 -> 分类（模糊匹配，包含关键词即可）
MERCHANT_RULES = {
    "成都地铁": "交通", "成都公交": "交通", "地铁": "交通", "公交": "交通",
    "滴滴出行": "交通", "滴滴": "交通", "哈啰出行": "交通", "哈啰": "交通",
    "高铁": "交通", "铁路": "交通", "12306": "交通", "打车": "交通",
    "星巴克": "餐饮", "紫光园": "餐饮", "豪客来": "餐饮", "蒋记抄手": "餐饮",
    "钟永庆水饺": "餐饮", "钱大妈": "餐饮", "麦当劳": "餐饮", "肯德基": "餐饮",
    "必胜客": "餐饮", "海底捞": "餐饮", "烤肉": "餐饮", "火锅": "餐饮",
    "奶茶": "餐饮", "茶": "餐饮", "咖啡": "餐饮", "面": "餐饮",
    "饺子": "餐饮", "抄手": "餐饮", "粉": "餐饮", "饭": "餐饮",
    "朴朴超市": "日用百货", "沃尔玛": "日用百货", "山姆会员": "日用百货",
    "好又多": "日用百货", "永辉": "日用百货", "大润发": "日用百货",
    "家乐福": "日用百货", "盒马": "日用百货",
    "携程旅行": "旅行", "携程": "旅行", "华住会": "旅行", "华住": "旅行",
    "酒店": "旅行", "机票": "旅行", "民宿": "旅行",
    "淘宝": "购物", "京东": "购物", "拼多多": "购物", "得物": "购物",
    "天猫": "购物",
    "中国移动": "通讯", "中国联通": "通讯", "中国电信": "通讯",
    "话费": "通讯", "流量": "通讯",
    "电影": "娱乐", "游戏": "娱乐", "KTV": "娱乐", "密室": "娱乐",
    "药房": "医疗健康", "医院": "医疗健康", "诊所": "医疗健康", "药店": "医疗健康",
    "财付通": "转账", "微信转账": "转账", "转账": "转账",
}


def _match_merchant(merchant: str) -> tuple[str, str]:
    """商户名匹配分类，返回 (category, confidence)
    优先级：确定性规则 > 本体知识库 > 待分类
    """
    if not merchant:
        return "待分类", "pending"
    for keyword, category in MERCHANT_RULES.items():
        if keyword in merchant:
            return category, "medium"
    # 本体兜底：长尾/新商户（ontology.yaml），只取顶层分类兼容现有体系
    try:
        from knowledge.query import get_ontology
        category, confidence, _ = get_ontology().get_category(merchant)
        if category:
            return category.split("/")[0], confidence
    except Exception:
        pass
    return "待分类", "pending"


# ── TSV 解析 ─────────────────────────────────────────

def _find_header(lines: list[str]) -> int:
    """找到表头行索引（以'交易时间'开头的行）"""
    for i, line in enumerate(lines):
        if line.startswith("交易时间"):
            return i
    return -1


def _parse_amount(raw: str) -> float:
    """解析金额字符串，处理逗号、空格、负号"""
    if not raw:
        return 0.0
    s = raw.strip().replace(",", "").replace(" ", "").replace("¥", "")
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


def _parse_datetime(raw: str) -> tuple[str, str, str]:
    """解析日期时间，返回 (datetime, date, year_month)"""
    s = raw.strip()
    # 修复 "2026-05-0310:58:52" -> "2026-05-03 10:58:52"
    m = re.match(r"(\d{4}-\d{2}-\d{2})(\d{2}:\d{2}:\d{2})", s)
    if m:
        s = f"{m.group(1)} {m.group(2)}"
    # 确保格式正确
    parts = s.split(" ")
    date_str = parts[0] if parts else s
    time_str = parts[1] if len(parts) > 1 else "00:00:00"
    dt = f"{date_str} {time_str}".strip()
    return dt, date_str, date_str[:7] if len(date_str) >= 7 else ""


# ── 加载函数 ─────────────────────────────────────────

def load_wechat(path: Path) -> list[Transaction]:
    """加载微信支付账单 TSV"""
    txns = []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    header_idx = _find_header(lines)
    if header_idx < 0:
        return txns

    reader = csv.reader(lines[header_idx:], delimiter="\t")
    header = next(reader, None)
    if not header:
        return txns

    # 列索引
    col = {name.strip(): i for i, name in enumerate(header)}

    for row in reader:
        if not row or not row[0].strip() or not re.match(r"\d{4}-\d{2}-\d{2}", row[0].strip()):
            continue
        if len(row) < len(header):
            continue

        dt, date, ym = _parse_datetime(row[col.get("交易时间", 0)])
        raw_dir = row[col.get("收/支", 4)].strip()
        direction = {"支出": "expense", "收入": "income"}.get(raw_dir, "neutral")
        amount = _parse_amount(row[col.get("金额(元)", 5)])
        merchant = row[col.get("交易对方", 2)].strip()
        desc = row[col.get("商品", 3)].strip()
        account = row[col.get("支付方式", 6)].strip()
        status = row[col.get("当前状态", 7)].strip()
        txn_id = row[col.get("交易单号", 8)].strip() if col.get("交易单号", 8) < len(row) else ""

        category, confidence = _match_merchant(merchant)

        txns.append(Transaction(
            id=f"wx_{len(txns)}",
            datetime=dt, date=date, year_month=ym,
            amount=amount, direction=direction,
            merchant=merchant, description=desc,
            category=category, category_confidence=confidence,
            source="wechat", account=account, status=status,
            transaction_id=txn_id,
        ))
    return txns


def load_alipay(path: Path) -> list[Transaction]:
    """加载支付宝交易明细 TSV"""
    txns = []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    header_idx = _find_header(lines)
    if header_idx < 0:
        return txns

    reader = csv.reader(lines[header_idx:], delimiter="\t")
    header = next(reader, None)
    if not header:
        return txns

    col = {name.strip(): i for i, name in enumerate(header)}

    for row in reader:
        if not row or not row[0].strip() or not re.match(r"\d{4}-\d{2}-\d{2}", row[0].strip()):
            continue
        if len(row) < len(header):
            continue

        dt, date, ym = _parse_datetime(row[col.get("交易时间", 0)])
        raw_dir = row[col.get("收/支", 5)].strip()
        direction = {"支出": "expense", "收入": "income"}.get(raw_dir, "neutral")
        amount = _parse_amount(row[col.get("金额", 6)])
        merchant = row[col.get("交易对方", 2)].strip()
        desc = row[col.get("商品说明", 4)].strip()
        alipay_cat = row[col.get("交易分类", 1)].strip()
        account = row[col.get("收/付款方式", 7)].strip()
        status = row[col.get("交易状态", 8)].strip()
        txn_id = row[col.get("交易订单号", 9)].strip() if col.get("交易订单号", 9) < len(row) else ""

        # 支付宝自带分类，置信度高
        category = ALIPAY_CATEGORY_MAP.get(alipay_cat, alipay_cat if alipay_cat else "待分类")
        confidence = "high" if category != "待分类" else "pending"
        # 如果支付宝没分类，再用商户名匹配
        if confidence == "pending":
            category, confidence = _match_merchant(merchant)

        txns.append(Transaction(
            id=f"ap_{len(txns)}",
            datetime=dt, date=date, year_month=ym,
            amount=amount, direction=direction,
            merchant=merchant, description=desc,
            category=category, category_confidence=confidence,
            source="alipay", account=account, status=status,
            transaction_id=txn_id,
        ))
    return txns


def load_bank(path: Path) -> list[Transaction]:
    """加载兴业银行交易明细 TSV"""
    txns = []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    header_idx = _find_header(lines)
    if header_idx < 0:
        return txns

    reader = csv.reader(lines[header_idx:], delimiter="\t")
    header = next(reader, None)
    if not header:
        return txns

    col = {name.strip(): i for i, name in enumerate(header)}

    for row in reader:
        if not row or not row[0].strip() or not re.match(r"\d{4}-\d{2}-\d{2}", row[0].strip()):
            continue
        if len(row) < len(header):
            continue

        dt, date, ym = _parse_datetime(row[col.get("交易时间", 0)])
        raw_dir = row[col.get("支/收", 3)].strip()
        direction = {"支": "expense", "收": "income"}.get(raw_dir, "neutral")
        amount = _parse_amount(row[col.get("交易金额", 4)])
        summary = row[col.get("摘要", 2)].strip()
        usage = row[col.get("交易用途", 6)].strip()
        counterparty = row[col.get("对方户名", 7)].strip()
        counter_acct = row[col.get("对方账户/对方银行", 8)].strip() if col.get("对方账户/对方银行", 8) < len(row) else ""

        # 去重: 银行侧的快捷支付记录（对方户名或对方账户含"财付通"/"支付宝"）是微信/支付宝的重复记录
        is_dup = "财付通" in counterparty or "支付宝" in counterparty or "财付通" in counter_acct or "支付宝" in counter_acct

        # 分类: 用摘要+对方户名匹配
        match_text = f"{summary} {counterparty} {usage}"
        category, confidence = _match_merchant(match_text)
        if confidence == "pending":
            # 用摘要分类
            summary_map = {"汇款汇入": "转入", "汇款汇出": "转出", "退款": "退款"}
            category = summary_map.get(summary, "待分类")
            confidence = "high" if category != "待分类" else "pending"

        merchant = counterparty if counterparty else summary
        desc = usage if usage else summary

        txns.append(Transaction(
            id=f"bk_{len(txns)}",
            datetime=dt, date=date, year_month=ym,
            amount=amount, direction=direction,
            merchant=merchant, description=desc,
            category=category, category_confidence=confidence,
            source="bank", account="兴业银行(2311)", status="成功",
            transaction_id="",
            is_duplicate=is_dup,
        ))
    return txns


def load_all(data_dir: Path) -> list[Transaction]:
    """加载全部三份账单，合并返回"""
    all_txns = []

    # 微信
    wechat_path = data_dir / "2_微信支付账单_20260426-20260726.txt"
    if wechat_path.exists():
        all_txns.extend(load_wechat(wechat_path))
        print(f"  微信: {len([t for t in all_txns if t.source == 'wechat'])} 笔")

    # 支付宝
    alipay_path = data_dir / "3_支付宝交易明细_20260426-20260726.txt"
    if alipay_path.exists():
        before = len(all_txns)
        all_txns.extend(load_alipay(alipay_path))
        print(f"  支付宝: {len(all_txns) - before} 笔")

    # 银行
    bank_path = data_dir / "4_兴业银行账单_20260426-20260726.txt"
    if bank_path.exists():
        before = len(all_txns)
        all_txns.extend(load_bank(bank_path))
        print(f"  银行: {len(all_txns) - before} 笔")

    return all_txns


# ── 快速测试 ─────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from config import get_data_dir

    print("加载数据...")
    data_dir = get_data_dir()
    txns = load_all(data_dir)
    print(f"\n总交易数: {len(txns)}")
    print(f"支出(去重后): {len([t for t in txns if t.direction == 'expense' and not t.is_duplicate])}")
    print(f"收入(去重后): {len([t for t in txns if t.direction == 'income' and not t.is_duplicate])}")
    print(f"中性: {len([t for t in txns if t.direction == 'neutral'])}")
    print(f"银行侧重复: {len([t for t in txns if t.is_duplicate])}")
    print(f"待分类: {len([t for t in txns if t.category == '待分类' and not t.is_duplicate])}")

    print("\n前 5 条:")
    for t in txns[:5]:
        print(f"  {t.datetime} | {t.direction:7s} | {t.amount:>10.2f} | {t.merchant[:15]:15s} | {t.category:6s} | {t.source}")
