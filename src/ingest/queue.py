"""
ingest/queue.py —— 乔姐的元数据确认队列。

《设计方案.md》§2.3：元数据抽取不可靠时，让人确认，不要让模型猜。
确认动作的副作用是一次性的，且只做一件事：把 meta_confirmed_by 写上。
助手永远不自动写这个字段。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime


def list_pending(conn: sqlite3.Connection) -> list[dict]:
    rs = conn.execute(
        """SELECT q.*, d.title, d.source_uri
           FROM metadata_review_queue q
           LEFT JOIN policy_document d ON d.id = q.doc_id
           WHERE q.status='pending' ORDER BY q.confidence ASC"""
    ).fetchall()
    out = []
    for r in rs:
        d = dict(r)
        d["proposal_obj"] = json.loads(r["proposal"]) if r["proposal"] else {}
        out.append(d)
    return out


def confirm_review(conn: sqlite3.Connection, review_id: str, *, by: str = "乔姐",
                   amendments: dict | None = None) -> dict:
    """乔姐点「确认」（可附带修正值）。确认后该版本才对检索可见。"""
    r = conn.execute(
        "SELECT * FROM metadata_review_queue WHERE id=? AND status='pending'", (review_id,)
    ).fetchone()
    if not r:
        return {"ok": False, "reason": "不存在该待确认项，或已处理"}

    prop = json.loads(r["proposal"] or "{}")
    if amendments:
        prop.update(amendments)

    now = datetime.now().astimezone().isoformat(timespec="seconds")

    conn.execute(
        """UPDATE metadata_review_queue
           SET status='confirmed', resolved_by=?, resolved_at=?, proposal=?
           WHERE id=?""",
        (by, now, json.dumps(prop, ensure_ascii=False), review_id),
    )
    conn.execute(
        """UPDATE policy_document
           SET meta_confirmed_by=?, meta_confirmed_at=?, meta_confidence=1.0,
               version=?, effective_from=?, supersedes_id=?
           WHERE id=?""",
        (by, now,
         prop.get("version"), prop.get("effective_from"), prop.get("supersedes_id"),
         r["doc_id"]),
    )
    conn.commit()

    return {
        "ok": True,
        "doc_id": r["doc_id"],
        "confirmed_by": by,
        "effective_from": prop.get("effective_from"),
        "version": prop.get("version"),
    }


def reject_review(conn: sqlite3.Connection, review_id: str, *, by: str = "乔姐",
                  reason: str = "") -> dict:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE metadata_review_queue SET status='rejected', resolved_by=?, resolved_at=? WHERE id=?",
        (by, now, review_id),
    )
    conn.commit()
    return {"ok": True, "review_id": review_id, "by": by, "reason": reason}
