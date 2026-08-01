"""本体效果演示脚本（MVP）

用真实账单数据对比「本体归一化 + 分类推理」前后效果：
1. 商户数变化（归一化合并）
2. 商户排行对比（合并分裂商户）
3. 待分类交易的命中率（本体能解决多少）
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # 使 agent 目录可导入

from config import get_data_dir
from data_loader import load_all
from knowledge.query import get_ontology


def main():
    # 加载真实交易
    txns = load_all(get_data_dir())
    valid = [t for t in txns if not t.is_duplicate]
    print(f"有效交易: {len(valid)} 笔\n")

    # ── 1. 商户归一化 ────────────────────────────────
    o = get_ontology()
    raw_merchants = {t.merchant for t in valid if t.merchant}
    normalized = {t.merchant: o.normalized_name(t.merchant) for t in valid if t.merchant}

    raw_count = len(raw_merchants)
    norm_count = len(set(normalized.values()))
    merged = [raw for raw, norm in normalized.items() if raw != norm]
    print("=" * 60)
    print("① 商户归一化（实体解析）")
    print("=" * 60)
    print(f"原始商户名数量: {raw_count}")
    print(f"归一化后商户数: {norm_count}  (合并了 {raw_count - norm_count} 个)")
    if merged:
        print("\n被合并的商户名（→ 实体）:")
        for raw in sorted(merged):
            print(f"  {raw}  →  {normalized[raw]}")

    # ── 2. 商户消费排行对比（Top 15） ────────────────
    spend_raw = Counter()
    spend_norm = Counter()
    for t in valid:
        if t.direction != "expense" or not t.merchant:
            continue
        spend_raw[t.merchant] += t.amount
        spend_norm[normalized[t.merchant]] += t.amount

    print("\n" + "=" * 60)
    print("② 支出商户排行（归一化前 vs 后，Top 15）")
    print("=" * 60)
    print(f"{'归一化前':30s} {'金额':>10s}  |  {'归一化后':22s} {'金额':>10s}")
    print("-" * 80)
    raw_top = spend_raw.most_common(15)
    norm_top = spend_norm.most_common(15)
    for i in range(15):
        r = raw_top[i] if i < len(raw_top) else ("", "")
        n = norm_top[i] if i < len(norm_top) else ("", "")
        print(f"{r[0]:30s} {r[1]:>10.2f}  |  {n[0]:22s} {n[1]:>10.2f}")

    # ── 3. 待分类交易命中率 ──────────────────────────
    pending = [t for t in valid if t.category == "待分类"]
    hit, miss = 0, 0
    miss_merchants = set()
    for t in pending:
        cat, conf, _ = o.get_category(t.merchant)
        if cat:
            hit += 1
        else:
            miss += 1
            if t.merchant:
                miss_merchants.add(t.merchant)

    print("\n" + "=" * 60)
    print("③ 待分类交易命中率（本体分类推理）")
    print("=" * 60)
    print(f"待分类交易: {len(pending)} 笔")
    print(f"本体可命中: {hit} 笔 ({hit / len(pending) * 100:.1f}%)" if pending else "本体可命中: 0 笔")
    if miss_merchants:
        print(f"\n未能命中（TOP 15，可用 LLM 兜底）:")
        for m in sorted(miss_merchants)[:15]:
            print(f"  {m}")


if __name__ == "__main__":
    main()
