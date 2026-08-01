"""本体查询层（MVP）

加载 ontology.yaml，提供：
- resolve_merchant: 商户字符串 → 实体（归一化）
- get_category: 商户字符串 → (分类, 置信度, 依据)（分类推理）
- 归一化聚合：把同名不同写的商户合并为实体

推理链（确定性，可解释）：
1. alias 精确命中（账单字符串 == alias）→ 该实体
2. keyword 包含命中（账单字符串 contains keyword）→ 该实体（门店变体归一化）
3. 未命中 → (None, "unresolved")
"""
from pathlib import Path
from typing import Optional

import yaml

ONTOLOGY_PATH = Path(__file__).parent / "ontology.yaml"
# 实例沉淀文件：用户/AI 确认过的分类写到这里，加载时与 ontology.yaml 合并
INSTANCES_PATH = Path(__file__).parent / "instances.yaml"


class MerchantEntity:
    """商户实体（本体实例）"""

    def __init__(self, name, entity_type, category, aliases=None, keywords=None):
        self.name = name
        self.type = entity_type          # brand / store / org / person
        self.category = category or ""   # 支持 父/子，如 餐饮/咖啡
        self.aliases = aliases or []
        self.keywords = keywords or []

    def __repr__(self):
        return f"<MerchantEntity {self.name} [{self.category}]>"


class Ontology:
    """最小本体：概念 + 实例 + 解析/推理"""

    def __init__(self, merchants=None, classes=None, relations=None):
        self.classes = classes or {}
        self.relations = relations or []
        self.merchants = merchants or []

        # 索引：alias -> entity（精确匹配）
        self._alias_index: dict[str, MerchantEntity] = {}
        # 索引：keyword -> entity（包含匹配）
        self._keyword_index: list[tuple[str, MerchantEntity]] = []
        for m in self.merchants:
            for a in m.aliases:
                self._alias_index.setdefault(str(a), m)
            for k in m.keywords:
                self._keyword_index.append((str(k), m))
        # keyword 按长度降序，保证"最具体优先"（如 西南财经大学 > 西财）
        self._keyword_index.sort(key=lambda x: len(x[0]), reverse=True)

    # ── 实体解析（归一化核心）────────────────────────

    def resolve_merchant(self, raw: str) -> Optional[MerchantEntity]:
        """账单商户字符串 → 实体。先精确别名，再关键词包含。"""
        raw = (raw or "").strip()
        if not raw:
            return None
        # 1. 精确别名
        if raw in self._alias_index:
            return self._alias_index[raw]
        # 2. 关键词包含（门店变体 → 品牌实体）
        for kw, entity in self._keyword_index:
            if kw in raw:
                return entity
        return None

    def normalized_name(self, raw: str) -> str:
        """归一化名称：能解析则返回实体名，否则原样返回"""
        entity = self.resolve_merchant(raw)
        return entity.name if entity else raw

    # ── 分类推理 ────────────────────────────────────

    def get_category(self, raw: str) -> tuple[str, str, str]:
        """返回 (分类, 置信度, 依据)
        置信度：high = 精确别名命中；medium = 关键词命中；"" = 未命中
        """
        raw = (raw or "").strip()
        if not raw:
            return ("", "", "")
        if raw in self._alias_index:
            e = self._alias_index[raw]
            return (e.category, "high", f"别名命中: {e.name}")
        for kw, e in self._keyword_index:
            if kw in raw:
                return (e.category, "medium", f"关键词命中: {kw} → {e.name}")
        return ("", "", "")

    def category_hierarchy(self, category: str) -> list[str]:
        """按 父/子 拆分，如 餐饮/咖啡 → [餐饮, 餐饮/咖啡]"""
        if not category:
            return []
        parts = category.split("/")
        return ["/".join(parts[: i + 1]) for i in range(len(parts))]

    # ── 加载 ─────────────────────────────────────────

    @classmethod
    def load(cls, path: Path = ONTOLOGY_PATH, instances_path: Path = INSTANCES_PATH) -> "Ontology":
        data = _load_yaml(path)
        merchants = []
        for m in data.get("merchants", []):
            merchants.append(MerchantEntity(
                name=m["name"],
                entity_type=m.get("type", "brand"),
                category=m.get("category", ""),
                aliases=m.get("aliases", []),
                keywords=m.get("keywords", []),
            ))
        # 合并实例沉淀（instances.yaml 覆盖 ontology.yaml 中同名实体）
        if instances_path.exists():
            inst = _load_yaml(instances_path)
            for m in inst.get("merchants", []):
                merchants.append(MerchantEntity(
                    name=m["name"],
                    entity_type=m.get("type", "brand"),
                    category=m.get("category", ""),
                    aliases=m.get("aliases", []),
                    keywords=m.get("keywords", []),
                ))
        return cls(
            merchants=merchants,
            classes=data.get("ontology", {}).get("classes", {}),
            relations=data.get("ontology", {}).get("relations", []),
        )


def _load_yaml(path: Path) -> dict:
    """读取 yaml，文件不存在或解析失败时返回空结构"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# 模块级单例（简单起见，直接加载）
_ont = None


def get_ontology() -> Ontology:
    global _ont
    if _ont is None:
        _ont = Ontology.load()
    return _ont


def upsert_merchant(merchant: str, category: str) -> bool:
    """知识沉淀：把 商户名→分类 写入 instances.yaml（持久化）

    供 AI 批量分类/Web 分类确认后调用，形成"越用越准"闭环。
    - 若该商户已能解析到实体（已在本体或沉淀中），跳过不重复写
    - 否则追加一条新实体（category 取顶层分类，保持与现有体系一致）
    返回 True 表示实际写入了沉淀。
    """
    global _ont
    merchant = (merchant or "").strip()
    if not merchant or not category:
        return False
    # 已能解析 → 无需沉淀
    if get_ontology().resolve_merchant(merchant):
        return False
    inst = _load_yaml(INSTANCES_PATH)
    merchants = inst.setdefault("merchants", [])
    # 避免重复写入同名实体
    if any(m.get("name") == merchant for m in merchants):
        return False
    merchants.append({
        "name": merchant,
        "type": "brand",
        "category": category.split("/")[0],  # 只存顶层分类
        "aliases": [merchant],
        "keywords": [],
    })
    try:
        with open(INSTANCES_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(inst, f, allow_unicode=True, sort_keys=False)
    except Exception:
        return False
    _ont = None  # 下次 get_ontology 重新加载
    return True


if __name__ == "__main__":
    # 快速自测
    o = get_ontology()
    for name in ["星巴克", "luckin coffee", "蒋记抄手王柳浪湾店", "山姆会员商店SamsCLUB",
                 "成都地铁", "携程旅行网", "不存在商户XYZ", "天府新区成都片区华阳钟永庆水饺店"]:
        print(f"{name:35s} → {o.normalized_name(name):10s} | {o.get_category(name)}")
