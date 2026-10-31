"""SQLite storage: queries (anonymous), price watches, user price feedback."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
  id INTEGER PRIMARY KEY, ts TEXT, source TEXT, chat_type TEXT,
  query_json TEXT, user_price REAL, conditions_json TEXT, verdict TEXT, best_site TEXT, best_price REAL
);
CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY, chat_id INTEGER, kind TEXT, query_json TEXT, conditions_json TEXT,
  baseline REAL, last_alert_price REAL, cancel_deadline TEXT, created TEXT,
  last_checked TEXT, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS site_ids (
  site TEXT, key TEXT, hotel_id TEXT, city_id TEXT, ts TEXT, PRIMARY KEY (site, key)
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY, ts TEXT, query_id INTEGER, shown_price REAL, seen_price REAL, note TEXT
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def _exec(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # --- queries ---
    def log_query(self, query: dict, source: str, chat_type: str, user_price: float | None = None,
                  conditions: dict | None = None) -> int:
        cur = self._exec(
            "INSERT INTO queries (ts, source, chat_type, query_json, user_price, conditions_json) VALUES (?,?,?,?,?,?)",
            (_now(), source, chat_type, json.dumps(query, ensure_ascii=False), user_price,
             json.dumps(conditions or {}, ensure_ascii=False)))
        return cur.lastrowid

    def set_verdict(self, qid: int, verdict: str, best_site: str | None, best_price: float | None) -> None:
        self._exec("UPDATE queries SET verdict=?, best_site=?, best_price=? WHERE id=?",
                   (verdict, best_site, best_price, qid))

    def get_query(self, qid: int) -> sqlite3.Row | None:
        with self.lock:
            return self.db.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()

    # --- watches ---
    def add_watch(self, chat_id: int, kind: str, query: dict, baseline: float | None,
                  conditions: dict | None = None, cancel_deadline: str | None = None) -> int:
        cur = self._exec(
            "INSERT INTO watches (chat_id, kind, query_json, conditions_json, baseline, cancel_deadline, created) "
            "VALUES (?,?,?,?,?,?,?)",
            (chat_id, kind, json.dumps(query, ensure_ascii=False), json.dumps(conditions or {}, ensure_ascii=False),
             baseline, cancel_deadline, _now()))
        return cur.lastrowid

    def active_watches(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute("SELECT * FROM watches WHERE active=1").fetchall()

    def chat_watches(self, chat_id: int) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute("SELECT * FROM watches WHERE active=1 AND chat_id=? ORDER BY id",
                                   (chat_id,)).fetchall()

    def update_watch(self, wid: int, **fields) -> None:
        fields["last_checked"] = fields.get("last_checked", _now())
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE watches SET {cols} WHERE id=?", (*fields.values(), wid))

    def stop_watch(self, wid: int, chat_id: int) -> bool:
        return self._exec("UPDATE watches SET active=0 WHERE id=? AND chat_id=?", (wid, chat_id)).rowcount > 0

    # --- hotel ids on other sites (resolve.py) ---
    def get_site_id(self, site: str, key: str) -> dict | None:
        """{'hotel_id', 'city_id'} if known, {} for a recent miss (retried after a day), None if never looked up."""
        with self.lock:
            row = self.db.execute(
                "SELECT hotel_id, city_id FROM site_ids WHERE site=? AND key=? AND "
                "(hotel_id IS NOT NULL OR replace(ts,'T',' ') >= datetime('now','localtime','-1 day'))", (site, key)).fetchone()
        if row is None:
            return None
        return {"hotel_id": row["hotel_id"], "city_id": row["city_id"]} if row["hotel_id"] else {}

    def set_site_id(self, site: str, key: str, ids: dict | None) -> None:
        ids = ids or {}
        self._exec("INSERT OR REPLACE INTO site_ids (site, key, hotel_id, city_id, ts) VALUES (?,?,?,?,?)",
                   (site, key, ids.get("hotel_id"), ids.get("city_id"), _now()))

    # --- feedback ---
    def add_feedback(self, qid: int | None, shown: float | None, seen: float | None, note: str = "") -> None:
        self._exec("INSERT INTO feedback (ts, query_id, shown_price, seen_price, note) VALUES (?,?,?,?,?)",
                   (_now(), qid, shown, seen, note))

    # --- stats ---
    def stats(self) -> dict:
        with self.lock:
            one = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
            return {
                "queries": one("SELECT COUNT(*) FROM queries"),
                "queries_7d": one("SELECT COUNT(*) FROM queries WHERE ts >= datetime('now','-7 days')"),
                "screenshots": one("SELECT COUNT(*) FROM queries WHERE source='screenshot'"),
                "links": one("SELECT COUNT(*) FROM queries WHERE source='link'"),
                "with_price": one("SELECT COUNT(*) FROM queries WHERE user_price IS NOT NULL"),
                "cheaper_found": one("SELECT COUNT(*) FROM queries WHERE verdict='cheaper'"),
                "active_watches": one("SELECT COUNT(*) FROM watches WHERE active=1"),
                "feedback": one("SELECT COUNT(*) FROM feedback"),
                "feedback_match": one("SELECT COUNT(*) FROM feedback WHERE shown_price IS NOT NULL AND "
                                      "seen_price IS NOT NULL AND ABS(seen_price-shown_price) <= shown_price*0.02"),
            }
