"""
青禾报销助手 · 数据层

设计要点（对应《落地细节.md》§1）：
  - policy_document 是「指针 + 元数据」，正文原文存在 policy_clause.text，一字不改
  - 双时间轴：effective_from / effective_to（业务时间）+ recorded_at（系统记录时间）
  - meta_confirmed_by 为空 = 该文档不得用于自动回答（硬闸门）
  - erp_expense_ticket 模拟业务系统，助手侧只能通过 v_my_ticket 视图访问
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "clause.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

-- ── 制度文档层（一个版本一条记录） ───────────────────────────
CREATE TABLE IF NOT EXISTS policy_document (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    version          TEXT NOT NULL,
    source_uri       TEXT NOT NULL,
    content_hash     TEXT NOT NULL,

    status           TEXT NOT NULL,        -- draft|effective|superseded|repealed|windowed
    doc_kind         TEXT NOT NULL,        -- STATIC|VERSIONED|EVENT
    publisher        TEXT,

    effective_from   TEXT NOT NULL,
    effective_to     TEXT,
    published_at     TEXT,
    recorded_at      TEXT NOT NULL,

    supersedes_id    TEXT REFERENCES policy_document(id),
    superseded_by    TEXT REFERENCES policy_document(id),

    scope_dept       TEXT,                 -- JSON array or NULL
    scope_region     TEXT,
    scope_grade      TEXT,

    meta_confidence  REAL,
    meta_confirmed_by TEXT,
    meta_confirmed_at TEXT,

    indexed_at       TEXT
);

-- ── 条款层（检索的最小单位，text 必须为原文） ────────────────
CREATE TABLE IF NOT EXISTS policy_clause (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL REFERENCES policy_document(id),
    clause_no     TEXT,
    heading       TEXT,
    text          TEXT NOT NULL,
    seq           INTEGER NOT NULL DEFAULT 0
);

-- ── 乔姐的元数据待确认队列 ───────────────────────────────────
CREATE TABLE IF NOT EXISTS metadata_review_queue (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT,
    kind        TEXT,          -- new_version|effective_date|supersede_relation|scope|repeal
    proposal    TEXT,          -- JSON
    evidence    TEXT,
    confidence  REAL,
    status      TEXT,          -- pending|confirmed|rejected
    resolved_by TEXT,
    resolved_at TEXT
);

-- ── 业务侧单据（模拟 ERP，助手无直接权限） ───────────────────
CREATE TABLE IF NOT EXISTS erp_expense_ticket (
    ticket_no          TEXT PRIMARY KEY,
    applicant_id       TEXT NOT NULL,
    applicant_name     TEXT NOT NULL,
    dept               TEXT NOT NULL,
    region             TEXT,
    amount             REAL,
    subject            TEXT,
    status_code        TEXT NOT NULL,
    status_label       TEXT NOT NULL,
    current_node       TEXT NOT NULL,
    node_entered_at    TEXT NOT NULL,
    submitted_at       TEXT NOT NULL,
    last_action_at     TEXT,
    last_return_reason TEXT,
    occurred_on        TEXT
);

-- ── 只读视图：不含金额/事由等敏感列 ─────────────────────────
CREATE VIEW IF NOT EXISTS v_my_ticket AS
SELECT
    ticket_no, applicant_id, applicant_name, dept, region,
    status_code, status_label, current_node, node_entered_at,
    submitted_at, last_action_at, last_return_reason, occurred_on
FROM erp_expense_ticket;

-- ── 转交单（助手 → 乔姐 / 周敏） ─────────────────────────────
CREATE TABLE IF NOT EXISTS escalation (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    asker_id    TEXT NOT NULL,
    asker_name  TEXT NOT NULL,
    question    TEXT NOT NULL,
    route_to    TEXT NOT NULL,             -- 乔姐 | 周敏
    reason      TEXT NOT NULL,
    context     TEXT,                      -- JSON: 检索到的候选条款
    as_of       TEXT,
    status      TEXT NOT NULL,             -- pending|answered
    answer      TEXT,
    answered_by TEXT,
    answered_at TEXT
);

-- ── 审计日志 ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    actor_employee TEXT NOT NULL,
    actor_name     TEXT,
    channel        TEXT,
    channel_userid TEXT,
    session_id     TEXT,
    intent         TEXT,
    query_text     TEXT,
    target         TEXT,
    result_code    TEXT,
    detail         TEXT,
    as_of          TEXT
);
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection, *, reset: bool = True) -> None:
    if reset:
        conn.executescript(
            "DROP VIEW IF EXISTS v_my_ticket;"
            "DROP TABLE IF EXISTS audit_log;"
            "DROP TABLE IF EXISTS escalation;"
            "DROP TABLE IF EXISTS v_my_ticket;"
            "DROP TABLE IF EXISTS erp_expense_ticket;"
            "DROP TABLE IF EXISTS metadata_review_queue;"
            "DROP TABLE IF EXISTS policy_clause;"
            "DROP TABLE IF EXISTS policy_document;"
        )
    conn.executescript(SCHEMA)
    conn.commit()


def rows(conn: sqlite3.Connection, sql: str, args: Any = ()) -> list[sqlite3.Row]:
    # 注意：不要 tuple(dict)，那会退化成键名元组
    return conn.execute(sql, args if isinstance(args, dict) else tuple(args)).fetchall()


def row(conn: sqlite3.Connection, sql: str, args: Any = ()) -> sqlite3.Row | None:
    return conn.execute(sql, args if isinstance(args, dict) else tuple(args)).fetchone()


def jload(s: str | None) -> Any:
    return json.loads(s) if s else None


def jdump(o: Any) -> str | None:
    return json.dumps(o, ensure_ascii=False) if o is not None else None
