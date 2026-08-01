"""SQLite 持久化层"""
import sqlite3
from pathlib import Path
from typing import Optional


class Database:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = Path(__file__).parent / "data" / "user_data.db"
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_tables()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def init_tables(self):
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL, type TEXT NOT NULL, balance REAL DEFAULT 0,
                    updated_at TEXT DEFAULT (datetime('now','localtime')),
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS fixed_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL, name TEXT NOT NULL, amount REAL NOT NULL,
                    day_of_month INTEGER, category TEXT DEFAULT '', note TEXT DEFAULT '',
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS transaction_classifications (
                    transaction_id TEXT PRIMARY KEY, category TEXT NOT NULL,
                    confirmed_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS category_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL, category TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL, parent_id INTEGER,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(name, parent_id),
                    FOREIGN KEY(parent_id) REFERENCES categories(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS gift_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    amount REAL NOT NULL DEFAULT 0,
                    travel_cost REAL NOT NULL DEFAULT 0,
                    event_date TEXT NOT NULL,
                    note TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    imported_at TEXT DEFAULT (datetime('now','localtime')),
                    count INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS debts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER,
                    reason TEXT DEFAULT '',
                    total_amount REAL NOT NULL,
                    remaining_amount REAL NOT NULL,
                    interest_rate REAL DEFAULT 0,
                    interest_type TEXT DEFAULT 'simple',
                    repayment_plan TEXT DEFAULT '',
                    due_date TEXT,
                    creditor TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS fixed_item_terms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixed_item_id INTEGER NOT NULL,
                    has_term INTEGER DEFAULT 0,
                    total_periods INTEGER DEFAULT 0,
                    paid_periods INTEGER DEFAULT 0,
                    start_month TEXT,
                    end_month TEXT,
                    period_amount REAL,
                    is_installment INTEGER DEFAULT 0,
                    auto_stop INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    FOREIGN KEY(fixed_item_id) REFERENCES fixed_items(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS installment_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term_id INTEGER NOT NULL,
                    period_number INTEGER NOT NULL,
                    due_month TEXT NOT NULL,
                    amount REAL NOT NULL,
                    paid_date TEXT,
                    status TEXT DEFAULT 'pending',
                    note TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(term_id) REFERENCES fixed_item_terms(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS savings_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    target_amount REAL NOT NULL,
                    current_amount REAL DEFAULT 0,
                    target_date TEXT,
                    account_id INTEGER,
                    monthly_save REAL DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    note TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS investment_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    institution TEXT DEFAULT '',
                    account_type TEXT NOT NULL,
                    principal REAL DEFAULT 0,
                    market_value REAL DEFAULT 0,
                    valuation_date TEXT,
                    risk_level TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT PRIMARY KEY,
                    datetime TEXT NOT NULL,
                    date TEXT NOT NULL,
                    year_month TEXT NOT NULL,
                    amount REAL NOT NULL,
                    direction TEXT NOT NULL,
                    merchant TEXT NOT NULL DEFAULT '',
                    description TEXT DEFAULT '',
                    category TEXT DEFAULT '待分类',
                    category_confidence TEXT DEFAULT 'pending',
                    source TEXT DEFAULT '',
                    account TEXT DEFAULT '',
                    status TEXT DEFAULT '',
                    transaction_id TEXT DEFAULT '',
                    is_duplicate INTEGER DEFAULT 0,
                    is_deleted INTEGER DEFAULT 0,
                    batch_id INTEGER,
                    updated_at TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY(batch_id) REFERENCES import_batches(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_txns_year_month ON transactions(year_month);
                CREATE INDEX IF NOT EXISTS idx_txns_date ON transactions(date);
                CREATE INDEX IF NOT EXISTS idx_txns_merchant ON transactions(merchant);
                CREATE INDEX IF NOT EXISTS idx_txns_direction ON transactions(direction);
                CREATE INDEX IF NOT EXISTS idx_txns_category ON transactions(category);

                -- 标签系统
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    color TEXT DEFAULT '#4299e1',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS transaction_tags (
                    transaction_id TEXT NOT NULL,
                    tag_id INTEGER NOT NULL,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    PRIMARY KEY (transaction_id, tag_id),
                    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_tx_tags_txn ON transaction_tags(transaction_id);
                CREATE INDEX IF NOT EXISTS idx_tx_tags_tag ON transaction_tags(tag_id);
            """)
            # 迁移：为旧表添加 is_active 列（如果尚未添加）
            try:
                conn.execute("ALTER TABLE category_rules ADD COLUMN is_active INTEGER DEFAULT 1")
            except sqlite3.OperationalError:
                pass  # 列已存在
            conn.commit()
        finally:
            conn.close()

    def get_setting(self, key, default=None):
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM user_settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default
        finally:
            conn.close()

    def set_setting(self, key, value):
        conn = self._connect()
        try:
            conn.execute("INSERT INTO user_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
            conn.commit()
        finally:
            conn.close()

    def get_accounts(self):
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY id DESC").fetchall()]
        finally:
            conn.close()

    def add_account(self, name, type, balance=0):
        conn = self._connect()
        try:
            cur = conn.execute("INSERT INTO accounts(name,type,balance) VALUES(?,?,?)", (name, type, float(balance)))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def update_account(self, account_id, name=None, type=None, balance=None):
        values, fields = [], []
        for field, value in (("name", name), ("type", type), ("balance", balance)):
            if value is not None:
                fields.append(f"{field}=?"); values.append(float(value) if field == "balance" else value)
        if not fields: return
        fields.append("updated_at=datetime('now','localtime')"); values.append(account_id)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id=?", values); conn.commit()
        finally: conn.close()

    def delete_account(self, account_id):
        conn = self._connect()
        try: conn.execute("DELETE FROM accounts WHERE id=?", (account_id,)); conn.commit()
        finally: conn.close()

    def recalc_accounts_from_transactions(self):
        """从交易数据一键统计各账户余额并更新"""
        conn = self._connect()
        try:
            # 获取所有交易按 source 的收支净额
            rows = conn.execute("""
                SELECT source,
                       COALESCE(SUM(CASE WHEN direction='income' THEN amount ELSE 0 END), 0) as total_income,
                       COALESCE(SUM(CASE WHEN direction='expense' THEN amount ELSE 0 END), 0) as total_expense
                FROM transactions
                WHERE is_deleted=0
                GROUP BY source
            """).fetchall()

            source_balance = {}
            total_balance = 0.0
            for r in rows:
                src = r["source"] or "其他"
                net = r["total_income"] - r["total_expense"]
                source_balance[src] = round(net, 2)
                total_balance += net

            # 更新现有账户：按名称关键词匹配 source
            accounts = self.get_accounts()
            updated_accounts = []
            # 匹配规则：source → account type 名称映射
            for acct in accounts:
                src_key = acct["type"].lower().replace(" ", "")
                matched_src = None
                for src in source_balance:
                    if src.lower().replace(" ", "") in src_key or src_key in src.lower().replace(" ", ""):
                        matched_src = src
                        break
                if matched_src:
                    conn.execute("UPDATE accounts SET balance=?, updated_at=datetime('now','localtime') WHERE id=?", (source_balance[matched_src], acct["id"]))
                    updated_accounts.append(acct["id"])
                    del source_balance[matched_src]

            # 未匹配的 balance 作为总余额记录到设置
            unmatched = sum(source_balance.values()) + total_balance - sum(
                self.get_accounts_balance() for _ in [None]  # 不重复查
            ) if not updated_accounts else 0

            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('current_balance',?)",
                         (str(round(total_balance, 2)),))

            conn.commit()
            return {
                "total_balance": round(total_balance, 2),
                "accounts_updated": len(updated_accounts),
                "source_detail": {r["source"] or "其他": round(r["total_income"] - r["total_expense"], 2) for r in rows},
            }
        finally:
            conn.close()

    def get_accounts_balance(self):
        """获取所有账户余额总和"""
        return sum(a.get("balance", 0) or 0 for a in self.get_accounts())

    def get_total_debt(self):
        """获取所有债务剩余总额"""
        return sum(d.get("remaining_amount", 0) or 0 for d in self.get_debts())

    def get_fixed_items(self, type=None):
        conn = self._connect()
        try:
            sql = """SELECT fi.*, fit.start_month, fit.end_month, fit.has_term, fit.total_periods, fit.paid_periods
                     FROM fixed_items fi
                     LEFT JOIN fixed_item_terms fit ON fi.id=fit.fixed_item_id AND fit.has_term=1 AND fit.status='active'
                     WHERE fi.is_active=1"""
            params = []
            if type:
                sql += " AND fi.type=?"
                params.append(type)
            sql += " ORDER BY fi.id DESC"
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally: conn.close()

    def add_fixed_item(self, type, name, amount, day_of_month=None, category='', note='', start_month=None, end_month=None):
        conn = self._connect()
        try:
            cur = conn.execute("INSERT INTO fixed_items(type,name,amount,day_of_month,category,note) VALUES(?,?,?,?,?,?)", (type,name,float(amount),day_of_month,category,note)); conn.commit()
            fid = cur.lastrowid
            # 如果有起止月份，自动创建期限配置
            if start_month or end_month:
                has_term = 1
                total_periods = 0
                if start_month and end_month:
                    sy, sm = map(int, start_month.split('-'))
                    ey, em = map(int, end_month.split('-'))
                    total_periods = (ey - sy) * 12 + (em - sm) + 1
                elif start_month:
                    total_periods = 999  # 无结束，长期
                conn.execute(
                    "INSERT INTO fixed_item_terms(fixed_item_id,has_term,total_periods,paid_periods,start_month,end_month,period_amount,is_installment,auto_stop) VALUES(?,?,?,?,?,?,?,?,?)",
                    (fid, has_term, total_periods, 0, start_month, end_month, float(amount) if end_month else None, 0, 1),
                )
                conn.commit()
            return fid
        finally: conn.close()

    def update_fixed_item(self, item_id, **kwargs):
        allowed = {k:v for k,v in kwargs.items() if k in {'type','name','amount','day_of_month','category','note','is_active'}}
        if not allowed: return
        fields = ', '.join(f"{k}=?" for k in allowed)
        conn = self._connect()
        try: conn.execute(f"UPDATE fixed_items SET {fields} WHERE id=?", [*allowed.values(), item_id]); conn.commit()
        finally: conn.close()

    # ══════════════════════════════════════════════════
    # 随礼管理（人情账本）
    # ══════════════════════════════════════════════════

    def get_gift_events(self, status=None):
        """获取随礼事件列表。status: 'paid'（已随）/ 'planned'（计划中）/ None（全部）"""
        conn = self._connect()
        try:
            sql = "SELECT * FROM gift_events"
            params = []
            if status == "paid":
                sql += " WHERE event_date < date('now','localtime')"
            elif status == "planned":
                sql += " WHERE event_date >= date('now','localtime')"
            sql += " ORDER BY event_date DESC"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally: conn.close()

    def add_gift_event(self, person, event_type, amount, event_date, travel_cost=0, note=''):
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO gift_events(person,event_type,amount,travel_cost,event_date,note) VALUES(?,?,?,?,?,?)",
                (person, event_type, float(amount), float(travel_cost or 0), event_date, note),
            )
            conn.commit()
            return cur.lastrowid
        finally: conn.close()

    def update_gift_event(self, event_id, **kwargs):
        allowed = {k:v for k,v in kwargs.items() if k in {'person','event_type','amount','travel_cost','event_date','note'}}
        if not allowed: return
        fields = ', '.join(f"{k}=?" for k in allowed)
        conn = self._connect()
        try: conn.execute(f"UPDATE gift_events SET {fields} WHERE id=?", [*allowed.values(), event_id]); conn.commit()
        finally: conn.close()

    def delete_gift_event(self, event_id):
        conn = self._connect()
        try: conn.execute("DELETE FROM gift_events WHERE id=?", (event_id,)); conn.commit()
        finally: conn.close()

    def get_gift_summary(self):
        """随礼汇总：
        - 总户数（去重对象）、总金额（礼金+交通）、已随/计划中金额
        - 每家聚合：对象 → 总礼金、次数、事件列表
        """
        events = self.get_gift_events()
        from datetime import date as _date
        today = _date.today().isoformat()

        paid = [e for e in events if e["event_date"] < today]
        planned = [e for e in events if e["event_date"] >= today]

        def total(evs):
            return round(sum(e["amount"] + e["travel_cost"] for e in evs), 2)

        # 每家聚合
        person_map = {}
        for e in events:
            p = person_map.setdefault(e["person"], {"person": e["person"], "total_amount": 0.0, "count": 0, "events": []})
            p["total_amount"] += e["amount"] + e["travel_cost"]
            p["count"] += 1
            p["events"].append({
                "event_type": e["event_type"], "amount": e["amount"],
                "travel_cost": e["travel_cost"], "event_date": e["event_date"], "note": e["note"],
            })
        persons = sorted(person_map.values(), key=lambda x: x["total_amount"], reverse=True)

        # 按事件类型聚合（只统计已随）
        type_map = {}
        for e in paid:
            t = type_map.setdefault(e["event_type"], {"event_type": e["event_type"], "total_amount": 0.0, "count": 0})
            t["total_amount"] += e["amount"] + e["travel_cost"]
            t["count"] += 1
        types = sorted(type_map.values(), key=lambda x: x["total_amount"], reverse=True)

        return {
            "total_persons": len(persons),
            "total_count": len(events),
            "total_amount": round(total(events), 2),
            "paid_amount": round(total(paid), 2),
            "planned_amount": round(total(planned), 2),
            "paid_count": len(paid),
            "planned_count": len(planned),
            "persons": persons,
            "types": types,
        }

    def get_fixed_items_for_month(self, year_month):
        """获取指定月份有效的固定收支（含期限过滤）"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT fi.*, fit.start_month, fit.end_month, fit.has_term
                   FROM fixed_items fi
                   LEFT JOIN fixed_item_terms fit ON fi.id=fit.fixed_item_id AND fit.has_term=1 AND fit.status='active'
                   WHERE fi.is_active=1
                   AND (
                       fit.id IS NULL  -- 无期限限制
                       OR (fit.start_month IS NULL OR fit.start_month<=?)
                       AND (fit.end_month IS NULL OR fit.end_month>=?)
                   )
                   ORDER BY fi.id DESC""",
                (year_month, year_month),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete_fixed_item(self, item_id):
        self.update_fixed_item(item_id, is_active=0)

    def get_classification(self, transaction_id):
        conn = self._connect()
        try:
            row = conn.execute("SELECT category FROM transaction_classifications WHERE transaction_id=?", (transaction_id,)).fetchone()
            return row["category"] if row else None
        finally: conn.close()

    def set_classification(self, transaction_id, category):
        conn = self._connect()
        try:
            conn.execute("INSERT INTO transaction_classifications(transaction_id,category) VALUES(?,?) ON CONFLICT(transaction_id) DO UPDATE SET category=excluded.category, confirmed_at=datetime('now','localtime')", (transaction_id, category)); conn.commit()
        finally: conn.close()

    def delete_classification(self, transaction_id):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM transaction_classifications WHERE transaction_id=?", (transaction_id,)); conn.commit()
        finally: conn.close()

    def reset_all_classifications(self):
        """删除所有交易分类记录"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM transaction_classifications")
            conn.commit()
        finally:
            conn.close()

    def upsert_rule(self, keyword, category):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM category_rules WHERE keyword=?", (keyword,))
            cur = conn.execute("INSERT INTO category_rules(keyword,category) VALUES(?,?)", (keyword, category))
            conn.commit()
            return cur.lastrowid
        finally: conn.close()

    def get_all_classifications(self):
        conn = self._connect()
        try: return {r['transaction_id']: r['category'] for r in conn.execute("SELECT transaction_id,category FROM transaction_classifications").fetchall()}
        finally: conn.close()

    def get_batch_classifications(self, transaction_ids):
        if not transaction_ids: return {}
        placeholders = ','.join('?' * len(transaction_ids)); conn = self._connect()
        try: return {r['transaction_id']:r['category'] for r in conn.execute(f"SELECT transaction_id,category FROM transaction_classifications WHERE transaction_id IN ({placeholders})", transaction_ids).fetchall()}
        finally: conn.close()

    def get_category_rules(self, active_only=False):
        conn = self._connect()
        try:
            sql = "SELECT * FROM category_rules"
            if active_only:
                sql += " WHERE is_active=1"
            sql += " ORDER BY id DESC"
            return [dict(r) for r in conn.execute(sql).fetchall()]
        finally: conn.close()

    def add_rule(self, keyword, category):
        conn = self._connect()
        try:
            cur = conn.execute("INSERT INTO category_rules(keyword,category) VALUES(?,?)", (keyword,category)); conn.commit(); return cur.lastrowid
        finally: conn.close()

    def update_rule(self, rule_id, keyword=None, category=None, is_active=None):
        updates = {}
        if keyword is not None: updates['keyword'] = keyword
        if category is not None: updates['category'] = category
        if is_active is not None: updates['is_active'] = 1 if is_active else 0
        if not updates: return
        conn = self._connect()
        try:
            conn.execute(f"UPDATE category_rules SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?", [*updates.values(), rule_id]); conn.commit()
        finally: conn.close()

    def delete_rule(self, rule_id):
        conn = self._connect()
        try: conn.execute("DELETE FROM category_rules WHERE id=?", (rule_id,)); conn.commit()
        finally: conn.close()

    def toggle_rule(self, rule_id):
        conn = self._connect()
        try:
            row = conn.execute("SELECT is_active FROM category_rules WHERE id=?", (rule_id,)).fetchone()
            if row:
                new_val = 0 if row['is_active'] else 1
                conn.execute("UPDATE category_rules SET is_active=? WHERE id=?", (new_val, rule_id))
                conn.commit()
                return new_val
            return None
        finally: conn.close()

    def match_rule(self, merchant):
        if not merchant: return None
        rules = self.get_category_rules(active_only=True)
        matches = [r for r in rules if r['keyword'] and r['keyword'] in merchant]
        return max(matches, key=lambda r: len(r['keyword']))['category'] if matches else None

    def get_categories(self):
        conn = self._connect()
        try: return [dict(r) for r in conn.execute("SELECT id,name,parent_id,created_at FROM categories ORDER BY COALESCE(parent_id,0),id").fetchall()]
        finally: conn.close()

    def add_category(self, name, parent_id=None):
        name = str(name or '').strip()
        if not name: raise ValueError('分类名称不能为空')
        conn = self._connect()
        try:
            cur = conn.execute("INSERT INTO categories(name,parent_id) VALUES(?,?)", (name,parent_id)); conn.commit(); return cur.lastrowid
        finally: conn.close()

    def delete_category(self, category_id):
        conn = self._connect()
        try: conn.execute("DELETE FROM categories WHERE id=?", (category_id,)); conn.commit()
        finally: conn.close()

    # ══════════════════════════════════════════════════
    # 标签系统
    # ══════════════════════════════════════════════════

    def get_all_tags(self):
        """获取所有标签"""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def add_tag(self, name, color="#4299e1"):
        """新增标签（已存在则返回已有 id）"""
        name = str(name or "").strip()
        if not name:
            raise ValueError("标签名不能为空")
        conn = self._connect()
        try:
            existing = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            if existing:
                return existing["id"]
            cur = conn.execute("INSERT INTO tags(name,color) VALUES(?,?)", (name, color))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def update_tag(self, tag_id, name=None, color=None):
        updates = {}
        if name is not None:
            updates["name"] = name.strip()
        if color is not None:
            updates["color"] = color
        if not updates:
            return
        fields = ", ".join(f"{k}=?" for k in updates)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE tags SET {fields} WHERE id=?", [*updates.values(), tag_id])
            conn.commit()
        finally:
            conn.close()

    def delete_tag(self, tag_id):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
            conn.commit()
        finally:
            conn.close()

    def get_transaction_tags(self, transaction_id):
        """获取一笔交易的所有标签"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT t.id, t.name, t.color FROM tags t "
                "JOIN transaction_tags tt ON t.id=tt.tag_id "
                "WHERE tt.transaction_id=? ORDER BY t.name",
                (transaction_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_batch_tags(self, transaction_ids):
        """批量获取多笔交易的标签，返回 {txn_id: [tags]}"""
        if not transaction_ids:
            return {}
        placeholders = ",".join("?" * len(transaction_ids))
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT tt.transaction_id, t.id, t.name, t.color "
                f"FROM transaction_tags tt JOIN tags t ON tt.tag_id=t.id "
                f"WHERE tt.transaction_id IN ({placeholders}) "
                f"ORDER BY tt.transaction_id, t.name",
                transaction_ids,
            ).fetchall()
            result = {}
            for r in rows:
                txn_id = r["transaction_id"]
                if txn_id not in result:
                    result[txn_id] = []
                result[txn_id].append({"id": r["id"], "name": r["name"], "color": r["color"]})
            return result
        finally:
            conn.close()

    def add_transaction_tag(self, transaction_id, tag_name=None, tag_id=None):
        """给交易打标签（按名称或按 id）"""
        if not tag_id and not tag_name:
            raise ValueError("tag_name 或 tag_id 必填一个")
        if not tag_id and tag_name:
            tag_id = self.add_tag(tag_name)
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO transaction_tags(transaction_id, tag_id) VALUES(?,?)",
                (transaction_id, tag_id),
            )
            conn.commit()
        finally:
            conn.close()

    def remove_transaction_tag(self, transaction_id, tag_id):
        """移除交易的某个标签"""
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM transaction_tags WHERE transaction_id=? AND tag_id=?",
                (transaction_id, tag_id),
            )
            conn.commit()
        finally:
            conn.close()

    def set_transaction_tags(self, transaction_id, tag_names):
        """全量设置交易标签（先清空再添加）"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM transaction_tags WHERE transaction_id=?", (transaction_id,))
            for name in tag_names:
                name = (name or "").strip()
                if not name:
                    continue
                # 确保标签存在
                existing = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
                if existing:
                    tag_id = existing["id"]
                else:
                    cur = conn.execute("INSERT INTO tags(name) VALUES(?)", (name,))
                    tag_id = cur.lastrowid
                conn.execute(
                    "INSERT OR IGNORE INTO transaction_tags(transaction_id, tag_id) VALUES(?,?)",
                    (transaction_id, tag_id),
                )
            conn.commit()
        finally:
            conn.close()

    def get_transactions_by_tag(self, tag_id, page=1, per_page=50):
        """按标签查询交易"""
        offset = (page - 1) * per_page
        conn = self._connect()
        try:
            count_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM transaction_tags tt "
                "JOIN transactions t ON tt.transaction_id=t.id "
                "WHERE tt.tag_id=? AND t.is_deleted=0",
                (tag_id,),
            ).fetchone()
            total = count_row["cnt"] if count_row else 0
            rows = conn.execute(
                "SELECT t.* FROM transaction_tags tt "
                "JOIN transactions t ON tt.transaction_id=t.id "
                "WHERE tt.tag_id=? AND t.is_deleted=0 "
                "ORDER BY t.datetime DESC LIMIT ? OFFSET ?",
                (tag_id, per_page, offset),
            ).fetchall()
            return [dict(r) for r in rows], total
        finally:
            conn.close()

    # ══════════════════════════════════════════════════
    # 交易数据 — 从 TSV 迁移到 DB
    # ══════════════════════════════════════════════════

    def get_tag_summary(self, start_date="", end_date=""):
        """获取所有标签的收支统计，可按交易日期范围过滤"""
        conn = self._connect()
        try:
            date_filter = ""
            if start_date and end_date:
                date_filter = "AND tx.date BETWEEN ? AND ?"
            elif start_date:
                date_filter = "AND tx.date >= ?"
            elif end_date:
                date_filter = "AND tx.date <= ?"
            rows = conn.execute(
                f"""SELECT t.id, t.name, t.color,
                          COUNT(tt.transaction_id) as count,
                          COALESCE(SUM(CASE WHEN tx.direction='expense' THEN tx.amount ELSE 0 END), 0) as total_expense,
                          COALESCE(SUM(CASE WHEN tx.direction='income' THEN tx.amount ELSE 0 END), 0) as total_income
                   FROM tags t
                   LEFT JOIN transaction_tags tt ON t.id=tt.tag_id
                   LEFT JOIN transactions tx ON tt.transaction_id=tx.id AND tx.is_deleted=0 {date_filter}
                   GROUP BY t.id, t.name, t.color
                   ORDER BY count DESC, t.name""",
                [x for x in (start_date, end_date) if x],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def import_transactions(self, txns, filename="", source=""):
        """批量导入交易记录，返回导入数量。txns 为 data_loader.Transaction 列表。"""
        conn = self._connect()
        try:
            # 创建批次
            cur = conn.execute(
                "INSERT INTO import_batches(filename,source,count) VALUES(?,?,?)",
                (filename, source, len(txns)),
            )
            batch_id = cur.lastrowid
            # 去重：已有 transaction_id + source 的交易视为重复
            existing = set()
            for r in conn.execute("SELECT transaction_id,source FROM transactions WHERE transaction_id!='' AND source!=''").fetchall():
                existing.add((r["transaction_id"], r["source"]))

            inserted = 0
            for t in txns:
                dup_key = (t.transaction_id or "", t.source or "")
                if dup_key[0] and dup_key[1] and dup_key in existing:
                    continue
                if dup_key[0] and dup_key[1]:
                    existing.add(dup_key)
                conn.execute(
                    """INSERT OR IGNORE INTO transactions
                    (id, datetime, date, year_month, amount, direction,
                     merchant, description, category, category_confidence,
                     source, account, status, transaction_id, is_duplicate, batch_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        t.id, t.datetime, t.date, t.year_month, t.amount, t.direction,
                        t.merchant, t.description, t.category, t.category_confidence,
                        t.source, t.account, t.status, t.transaction_id,
                        1 if t.is_duplicate else 0, batch_id,
                    ),
                )
                if conn.total_changes > 0:
                    inserted += 1
            # 更新批次计数
            conn.execute("UPDATE import_batches SET count=? WHERE id=?", (inserted, batch_id))
            conn.commit()
            return inserted
        finally:
            conn.close()

    def get_all_transactions(self, include_deleted=False):
        """获取所有交易记录，返回 Transaction 兼容的 dict 列表。"""
        conn = self._connect()
        try:
            sql = "SELECT * FROM transactions"
            if not include_deleted:
                sql += " WHERE is_deleted=0"
            sql += " ORDER BY datetime DESC"
            return [dict(r) for r in conn.execute(sql).fetchall()]
        finally:
            conn.close()

    def query_transactions_db(self, category=None, merchant=None, direction=None,
                              start_date=None, end_date=None, sort_by="datetime",
                              sort_order="desc", page=1, per_page=50,
                              include_deleted=False, year_month=None,
                              exact_merchant=False):
        """从 DB 查询交易记录，支持分页、筛选、排序。返回 (items, total)

        参数 exact_merchant=True 时，merchant 使用精确匹配（=）而非 LIKE。
        """
        conditions = []
        params = []

        if not include_deleted:
            conditions.append("is_deleted=0")

        if year_month:
            conditions.append("year_month=?")
            params.append(year_month)
        if category:
            conditions.append("category=?")
            params.append(category)
        if merchant:
            if exact_merchant:
                conditions.append("merchant=?")
                params.append(merchant)
            else:
                conditions.append("merchant LIKE ?")
                params.append(f"%{merchant}%")
        if direction:
            conditions.append("direction=?")
            params.append(direction)
        if start_date:
            conditions.append("date>=?")
            params.append(start_date)
        if end_date:
            conditions.append("date<=?")
            params.append(end_date)

        where = " AND ".join(conditions) if conditions else "1=1"

        # 排序
        sort_col = "datetime" if sort_by == "datetime" else "amount"
        order = "DESC" if sort_order.lower() == "desc" else "ASC"

        conn = self._connect()
        try:
            # 总数
            count_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM transactions WHERE {where}", params
            ).fetchone()
            total = count_row["cnt"] if count_row else 0

            # 分页
            offset = (page - 1) * per_page
            rows = conn.execute(
                f"SELECT * FROM transactions WHERE {where} ORDER BY {sort_col} {order} LIMIT ? OFFSET ?",
                [*params, per_page, offset],
            ).fetchall()
            return [dict(r) for r in rows], total
        finally:
            conn.close()

    def get_transaction(self, txn_id):
        """获取单条交易"""
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_transaction(self, txn_id, **kwargs):
        """更新交易字段。允许字段：amount, merchant, description, account, date, datetime"""
        allowed = {"amount", "merchant", "description", "account", "date", "datetime"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = "datetime('now','localtime')"
        set_clause = ", ".join(f"{k}=?" if k != "updated_at" else f"{k}=datetime('now','localtime')" for k in updates)
        values = [v for k, v in updates.items() if k != "updated_at"]
        values.append(txn_id)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE transactions SET {set_clause} WHERE id=?", values)
            conn.commit()
        finally:
            conn.close()

    def delete_transaction(self, txn_id):
        """软删除交易"""
        conn = self._connect()
        try:
            conn.execute("UPDATE transactions SET is_deleted=1, updated_at=datetime('now','localtime') WHERE id=?", (txn_id,))
            conn.commit()
        finally:
            conn.close()

    def find_duplicate_pairs(self, time_window_minutes=1):
        """
        查找重复交易对：相同金额、相近时间（默认1分钟内）、相似商户的跨渠道重复交易。
        返回列表，每个元素是 {"pair": [txn1, txn2], "type": "cross_source"}
        """
        conn = self._connect()
        try:
            # 获取所有非删除交易
            txns = conn.execute("""
                SELECT id, datetime, date, amount, direction, merchant, description, source, account
                FROM transactions
                WHERE is_deleted=0
                ORDER BY ABS(amount), datetime
            """).fetchall()
            txns = [dict(r) for r in txns]

            pairs = []
            used = set()

            # 按金额分组（取绝对值，收入为正支出为负但金额相同）
            groups = {}
            for t in txns:
                key = abs(t["amount"])
                groups.setdefault(key, []).append(t)

            for amount, group in groups.items():
                if len(group) < 2:
                    continue
                for i in range(len(group)):
                    if group[i]["id"] in used:
                        continue
                    for j in range(i + 1, len(group)):
                        if group[j]["id"] in used:
                            continue
                        a, b = group[i], group[j]

                        # 时间差检查（datetime 格式: YYYY-MM-DD HH:MM:SS）
                        try:
                            from datetime import datetime as dt_mod
                            ta = dt_mod.strptime(a["datetime"][:19], "%Y-%m-%d %H:%M:%S") if " " in a["datetime"] else dt_mod.strptime(a["datetime"][:10], "%Y-%m-%d")
                            tb = dt_mod.strptime(b["datetime"][:19], "%Y-%m-%d %H:%M:%S") if " " in b["datetime"] else dt_mod.strptime(b["datetime"][:10], "%Y-%m-%d")
                            diff = abs((ta - tb).total_seconds())
                        except (ValueError, IndexError):
                            diff = 999999

                        if diff > time_window_minutes * 60:
                            continue

                        # 商户名相似度检查（包含关系或编辑距离较小）
                        ma = (a["merchant"] or "").strip().lower()
                        mb = (b["merchant"] or "").strip().lower()
                        if not ma or not mb:
                            continue
                        if ma == mb or ma in mb or mb in ma:
                            # 找到一对重复
                            pair_type = "cross_source" if a.get("source") != b.get("source") else "same_source"
                            pairs.append({
                                "pair": [a, b],
                                "type": pair_type,
                                "time_diff_seconds": int(diff),
                            })
                            used.add(a["id"])
                            used.add(b["id"])
                            break

            return pairs
        finally:
            conn.close()

    def batch_delete_transactions(self, ids):
        """批量软删除交易"""
        if not ids:
            return 0
        conn = self._connect()
        try:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"UPDATE transactions SET is_deleted=1, updated_at=datetime('now','localtime') WHERE id IN ({placeholders})", ids)
            conn.commit()
            return len(ids)
        finally:
            conn.close()

    def restore_transaction(self, txn_id):
        """恢复已删除交易"""
        conn = self._connect()
        try:
            conn.execute("UPDATE transactions SET is_deleted=0, updated_at=datetime('now','localtime') WHERE id=?", (txn_id,))
            conn.commit()
        finally:
            conn.close()

    def get_import_batches(self):
        """获取导入历史"""
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM import_batches ORDER BY id DESC").fetchall()]
        finally:
            conn.close()

    # ══════════════════════════════════════════════════
    # 储蓄目标
    # ══════════════════════════════════════════════════

    def get_savings_goals(self):
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM savings_goals WHERE status!='deleted' ORDER BY id DESC").fetchall()]
        finally:
            conn.close()

    def add_savings_goal(self, name, target_amount, current_amount=0, target_date=None, account_id=None, monthly_save=0, note=''):
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO savings_goals(name,target_amount,current_amount,target_date,account_id,monthly_save,note) VALUES(?,?,?,?,?,?,?)",
                (name, float(target_amount), float(current_amount), target_date, account_id, float(monthly_save), note),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def update_savings_goal(self, goal_id, **kwargs):
        allowed = {k: v for k, v in kwargs.items() if k in ('name','target_amount','current_amount','target_date','account_id','monthly_save','status','note')}
        if not allowed:
            return
        fields = ', '.join(f"{k}=?" for k in allowed)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE savings_goals SET {fields} WHERE id=?", [*allowed.values(), goal_id])
            conn.commit()
        finally:
            conn.close()

    def delete_savings_goal(self, goal_id):
        self.update_savings_goal(goal_id, status='deleted')

    # ══════════════════════════════════════════════════
    # 理财账户
    # ══════════════════════════════════════════════════

    def get_investment_accounts(self):
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM investment_accounts WHERE is_active=1 ORDER BY id DESC").fetchall()]
        finally:
            conn.close()

    def add_investment_account(self, name, account_type, institution='', principal=0, market_value=0, valuation_date=None, risk_level='', note=''):
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO investment_accounts(name,institution,account_type,principal,market_value,valuation_date,risk_level,note) VALUES(?,?,?,?,?,?,?,?)",
                (name, institution, account_type, float(principal), float(market_value), valuation_date, risk_level, note),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def update_investment_account(self, inv_id, **kwargs):
        allowed = {k: v for k, v in kwargs.items() if k in ('name','institution','account_type','principal','market_value','valuation_date','risk_level','note','is_active')}
        if not allowed:
            return
        fields = ', '.join(f"{k}=?" for k in allowed)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE investment_accounts SET {fields} WHERE id=?", [*allowed.values(), inv_id])
            conn.commit()
        finally:
            conn.close()

    def delete_investment_account(self, inv_id):
        self.update_investment_account(inv_id, is_active=0)

    # ══════════════════════════════════════════════════
    # 债务管理
    # ══════════════════════════════════════════════════

    def get_debts(self):
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM debts WHERE is_active=1 ORDER BY id DESC").fetchall()]
        finally:
            conn.close()

    def add_debt(self, total_amount, remaining_amount=None, account_id=None, reason='', interest_rate=0, interest_type='simple', repayment_plan='', due_date=None, creditor=''):
        if remaining_amount is None:
            remaining_amount = total_amount
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO debts(account_id,reason,total_amount,remaining_amount,interest_rate,interest_type,repayment_plan,due_date,creditor) VALUES(?,?,?,?,?,?,?,?,?)",
                (account_id, reason, float(total_amount), float(remaining_amount), float(interest_rate), interest_type, repayment_plan, due_date, creditor),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def update_debt(self, debt_id, **kwargs):
        allowed = {k: v for k, v in kwargs.items() if k in ('account_id','reason','total_amount','remaining_amount','interest_rate','interest_type','repayment_plan','due_date','creditor','status','is_active')}
        if not allowed:
            return
        fields = ', '.join(f"{k}=?" for k in allowed)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE debts SET {fields} WHERE id=?", [*allowed.values(), debt_id])
            conn.commit()
        finally:
            conn.close()

    def delete_debt(self, debt_id):
        conn = self._connect()
        try:
            conn.execute("UPDATE debts SET is_active=0 WHERE id=?", (debt_id,))
            conn.commit()
        finally:
            conn.close()

    def repay_debt(self, debt_id, repay_amount, repay_date=None):
        """偿还债务"""
        conn = self._connect()
        try:
            debt = conn.execute("SELECT * FROM debts WHERE id=?", (debt_id,)).fetchone()
            if not debt:
                return None
            remaining = float(debt['remaining_amount']) - float(repay_amount)
            if remaining < 0:
                remaining = 0
            status = 'closed' if remaining <= 0 else 'active'
            conn.execute("UPDATE debts SET remaining_amount=?, status=?, due_date=COALESCE(?,due_date) WHERE id=?", (remaining, status, repay_date, debt_id))
            conn.commit()
            return {'id': debt_id, 'remaining': remaining, 'status': status}
        finally:
            conn.close()

    # ══════════════════════════════════════════════════
    # 固定收支期限管理 & 分期付款
    # ══════════════════════════════════════════════════

    def add_fixed_item_term(self, fixed_item_id, has_term=0, total_periods=0, paid_periods=0, start_month=None, end_month=None, period_amount=None, is_installment=0, auto_stop=0):
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO fixed_item_terms(fixed_item_id,has_term,total_periods,paid_periods,start_month,end_month,period_amount,is_installment,auto_stop) VALUES(?,?,?,?,?,?,?,?,?)",
                (fixed_item_id, 1 if has_term else 0, total_periods, paid_periods, start_month, end_month, period_amount, 1 if is_installment else 0, 1 if auto_stop else 0),
            )
            tid = cur.lastrowid
            # 如果是分期付款，自动生成各期记录
            if is_installment and total_periods > 0 and start_month and period_amount:
                conn.execute("DELETE FROM installment_payments WHERE term_id=?", (tid,))
                from datetime import datetime as dt_mod
                sy, sm = map(int, start_month.split('-'))
                for i in range(total_periods):
                    ym = f"{sy + (sm + i - 1) // 12:04d}-{(sm + i - 1) % 12 + 1:02d}"
                    conn.execute(
                        "INSERT INTO installment_payments(term_id,period_number,due_month,amount,status) VALUES(?,?,?,?,'pending')",
                        (tid, i + 1, ym, float(period_amount)),
                    )
            conn.commit()
            return tid
        finally:
            conn.close()

    def get_fixed_item_term(self, fixed_item_id):
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM fixed_item_terms WHERE fixed_item_id=? ORDER BY id DESC", (fixed_item_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_fixed_item_term(self, term_id, **kwargs):
        allowed = {k: v for k, v in kwargs.items() if k in ('has_term','total_periods','paid_periods','start_month','end_month','period_amount','is_installment','auto_stop','status')}
        if not allowed:
            return
        fields = ', '.join(f"{k}=?" for k in allowed)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE fixed_item_terms SET {fields} WHERE id=?", [*allowed.values(), term_id])
            conn.commit()
        finally:
            conn.close()

    def get_installment_payments(self, term_id=None):
        conn = self._connect()
        try:
            if term_id:
                rows = conn.execute("SELECT * FROM installment_payments WHERE term_id=? ORDER BY period_number ASC", (term_id,)).fetchall()
            else:
                rows = conn.execute("SELECT ip.*, fit.fixed_item_id FROM installment_payments ip JOIN fixed_item_terms fit ON ip.term_id=fit.id ORDER BY ip.due_month ASC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_installment_paid(self, payment_id, paid_date=None):
        conn = self._connect()
        try:
            p = conn.execute("SELECT * FROM installment_payments WHERE id=?", (payment_id,)).fetchone()
            if not p:
                return None
            from datetime import datetime as dt_mod
            if not paid_date:
                paid_date = dt_mod.now().strftime("%Y-%m-%d")
            conn.execute("UPDATE installment_payments SET status='paid', paid_date=? WHERE id=?", (paid_date, payment_id))
            # 同步更新 term 的 paid_periods
            term = conn.execute("SELECT * FROM fixed_item_terms WHERE id=?", (p['term_id'],)).fetchone()
            if term:
                paid_count = conn.execute("SELECT COUNT(*) as cnt FROM installment_payments WHERE term_id=? AND status='paid'", (p['term_id'],)).fetchone()['cnt']
                total = term['total_periods']
                status = 'completed' if paid_count >= total else 'active'
                conn.execute("UPDATE fixed_item_terms SET paid_periods=?, status=? WHERE id=?", (paid_count, status, p['term_id']))
            conn.commit()
            return {'id': payment_id, 'paid_date': paid_date}
        finally:
            conn.close()

    def get_term_reminders(self):
        """获取期限相关的提醒"""
        reminders = []
        conn = self._connect()
        try:
            # 1. 即将到期的分期
            from datetime import datetime as dt_mod
            today = dt_mod.now().strftime("%Y-%m")
            rows = conn.execute(
                "SELECT ip.*, fit.fixed_item_id, fi.name as item_name FROM installment_payments ip "
                "JOIN fixed_item_terms fit ON ip.term_id=fit.id "
                "JOIN fixed_items fi ON fit.fixed_item_id=fi.id "
                "WHERE ip.status='pending' AND ip.due_month<=? ORDER BY ip.due_month ASC",
                (today,),
            ).fetchall()
            overdue = [r for r in rows if r['due_month'] < today]
            due = [r for r in rows if r['due_month'] == today]
            if overdue:
                reminders.append({'type': 'installment_overdue', 'message': f'有 {len(overdue)} 笔分期已逾期', 'count': len(overdue)})
            if due:
                reminders.append({'type': 'installment_due', 'message': f'本月有 {len(due)} 笔分期待还', 'count': len(due)})

            # 2. 即将到期的固定期限项
            expiring = conn.execute(
                "SELECT fi.* FROM fixed_items fi JOIN fixed_item_terms fit ON fi.id=fit.fixed_item_id "
                "WHERE fi.is_active=1 AND fit.has_term=1 AND fit.end_month<=? AND fit.auto_stop=1 AND fit.status='active'",
                (today,),
            ).fetchall()
            if expiring:
                reminders.append({'type': 'term_expiring', 'message': f'有 {len(expiring)} 项固定收支即将到期', 'items': [r['name'] for r in expiring]})
        finally:
            conn.close()
        return reminders

    # ══════════════════════════════════════════════════
    # 到账确认状态 — 扩展 fixed_items 支持 income 确认
    # ══════════════════════════════════════════════════

    def confirm_income(self, item_id, actual_amount=None, actual_date=None):
        """确认固定收入到账。将固定收入项标记为已确认，并写入交易记录。"""
        conn = self._connect()
        try:
            item = conn.execute("SELECT * FROM fixed_items WHERE id=?", (item_id,)).fetchone()
            if not item:
                return None
            item = dict(item)
            if item['type'] != 'income':
                return None

            amount = actual_amount if actual_amount is not None else item['amount']
            confirm_date = actual_date if actual_date else ''

            # 标记为已确认（使用 note 字段记录确认信息）
            note = item.get('note', '') or ''
            if 'confirmed:' not in note:
                note = note + f" | confirmed:{confirm_date}" if note else f"confirmed:{confirm_date}"
            conn.execute("UPDATE fixed_items SET note=? WHERE id=?", (note, item_id))
            conn.commit()
            return {'id': item_id, 'name': item['name'], 'amount': amount, 'confirmed_date': confirm_date}
        finally:
            conn.close()

    def get_pending_income(self):
        """获取待确认的收入计划"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM fixed_items WHERE type='income' AND is_active=1 AND (note IS NULL OR note NOT LIKE '%confirmed:%') ORDER BY day_of_month ASC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_reminders(self):
        """获取待处理提醒列表"""
        reminders = []

        # 1. 待分类交易数量
        classified_ids = set()
        for r in self._connect().execute("SELECT transaction_id FROM transaction_classifications").fetchall():
            classified_ids.add(r['transaction_id'])
        pending_count = 0
        for t in self.get_all_transactions():
            if t['category'] == '待分类' and t['id'] not in classified_ids and not t['is_duplicate'] and not t['is_deleted']:
                pending_count += 1
        if pending_count > 0:
            reminders.append({'type': 'pending_category', 'message': f'有 {pending_count} 条交易待分类', 'count': pending_count})

        # 2. 待确认收入
        pending_income = self.get_pending_income()
        if pending_income:
            reminders.append({'type': 'pending_income', 'message': f'有 {len(pending_income)} 笔收入待确认到账', 'count': len(pending_income)})

        # 3. 低余额提醒
        balance_str = self.get_setting('current_balance', '0')
        safety_str = self.get_setting('safety_balance', '0')
        try:
            balance = float(balance_str)
            safety = float(safety_str)
        except (ValueError, TypeError):
            balance = 0
            safety = 0
        if safety > 0 and balance < safety:
            reminders.append({'type': 'low_balance', 'message': f'当前余额 ¥{balance:.2f} 低于安全线 ¥{safety:.2f}', 'balance': balance, 'safety': safety})

        # 4. 临近随礼提醒（7 天内）
        try:
            from datetime import date as _date, timedelta as _td
            today = _date.today()
            soon = (today + _td(days=7)).isoformat()
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM gift_events WHERE event_date >= ? AND event_date <= ? ORDER BY event_date ASC",
                    (today.isoformat(), soon),
                ).fetchall()
            finally:
                conn.close()
            for r in rows:
                days_left = (_date.fromisoformat(r["event_date"]) - today).days
                label = "今天" if days_left == 0 else f"{days_left} 天后"
                reminders.append({
                    'type': 'gift_upcoming',
                    'message': f'🎁 {r["person"]}的{r["event_type"]}（{r["event_date"]}）{label}，预计礼金 ¥{float(r["amount"]) + float(r["travel_cost"] or 0):.2f}',
                    'event_id': r["id"], 'event_date': r["event_date"],
                })
        except Exception:
            pass

        return reminders + self.get_term_reminders()

    def migrate_from_tsv(self):
        """从 TSV 文件加载交易数据到 DB。返回 True 如果有数据导入，否则 False。"""
        import sys
        from pathlib import Path
        # 延迟导入避免循环
        agent_dir = Path(__file__).parent.parent
        if str(agent_dir) not in sys.path:
            sys.path.insert(0, str(agent_dir))
        from data_loader import load_all
        from config import get_data_dir

        # 检查 DB 是否已有数据
        conn = self._connect()
        try:
            count = conn.execute("SELECT COUNT(*) as cnt FROM transactions").fetchone()["cnt"]
        finally:
            conn.close()
        if count > 0:
            return False  # 已有数据，不重复迁移

        data_dir = get_data_dir()
        txns = load_all(data_dir)
        if not txns:
            return False
        self.import_transactions(txns, filename="TSV 迁移", source="tsv_migration")
        return True
