"""Tiny SQLite helper. No ORM, so beginners can read every query."""
import json
import os
import sqlite3
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, email TEXT,
  history TEXT,            -- JSON list: days late of past invoices (negative = early)
  cash_now_pct REAL,       -- simulated payer: share of invoice they can pay right now
  max_days INTEGER         -- simulated payer: must be fully settled within N days
);
CREATE TABLE IF NOT EXISTS invoices(
  id INTEGER PRIMARY KEY AUTOINCREMENT, paypal_id TEXT, client_id INTEGER,
  description TEXT, amount REAL, due_date TEXT, status TEXT,
  parent_id INTEGER, created TEXT
);
CREATE TABLE IF NOT EXISTS actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER, type TEXT,
  payload TEXT, status TEXT, created TEXT, decided TEXT, result TEXT
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, event TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS expenses(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, amount REAL, date TEXT
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def _conn():
    folder = os.path.dirname(config.DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(SCHEMA)


def query(sql, params=()):
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with _conn() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audit(actor, event, detail=None):
    """Every important thing the agent or a human does is written here."""
    execute("INSERT INTO audit(ts, actor, event, detail) VALUES (?,?,?,?)",
            (now(), actor, event, json.dumps(detail, default=str) if detail is not None else None))


def get_setting(key, default=None):
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def set_setting(key, value):
    execute("INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def wipe():
    with _conn() as c:
        for t in ("clients", "invoices", "actions", "audit", "expenses", "settings"):
            c.execute(f"DELETE FROM {t}")
