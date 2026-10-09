"""
seed.py —— 建库并灌入 demo 数据。

元数据来源：乔姐在源文档头部写的声明（版本、施行日期、废止日期、适用范围）。
助手在此基础上做两件推断，其余一律不碰：
  1. 版本链：同一制度家族按施行日期排序，前一版 effective_to = 后一版施行日 -1 天
  2. 待确认：新版本入库时生成候选项，未经乔姐确认不得用于回答

市内交通费管理办法 v2 被刻意留作「乔姐尚未确认」，用于演示硬闸门。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

from .db import connect, init_db, jdump
from .ingest.parse import ParsedDoc, first, load_all, parse_date

TODAY = date(2026, 10, 9)

# 这一份不预确认 —— 留给乔姐工作台演示
PENDING_CONFIRM = {"市内交通费管理办法_v2"}
CONFIRMED_BY = "乔姐"
CONFIRMED_AT = "2026-10-08T09:12:00+08:00"


def _status_from_source(d: ParsedDoc) -> str:
    raw = first(d.body_fields, "状态") or ""
    if "限时有效" in raw:
        return "windowed"
    if "已废止" in raw:
        return "superseded"
    if "现行有效" in raw:
        return "effective"
    return "effective"


def _kind_from_source(d: ParsedDoc) -> str:
    raw = first(d.body_fields, "状态") or ""
    has_expiry = bool(first(d.body_fields, "有效期至"))
    if has_expiry or "限时有效" in raw:
        return "EVENT"
    if first(d.body_fields, "版本号"):
        return "VERSIONED"
    return "STATIC"


def _parse_scope(d: ParsedDoc) -> tuple[list[str] | None, list[str] | None, str | None]:
    """「适用范围：部门＝销售部，地区＝华东」→ (["销售部"], ["华东"], 原文)"""
    raw = first(d.body_fields, "适用范围")
    if not raw:
        return None, None, None
    dept = re_dept(raw)
    region = re_region(raw)
    return dept or None, region or None, raw


def re_dept(s: str) -> list[str]:
    import re
    return re.findall(r"部门[＝=:：]\s*([^，,；;]+)", s)


def re_region(s: str) -> list[str]:
    import re
    return re.findall(r"地区[＝=:：]\s*([^，,；;]+)", s)


def build_documents(docs: list[ParsedDoc]) -> list[dict]:
    """组装 policy_document 记录，并完成版本链推断。"""
    # 按家族分组
    families: dict[str, list[ParsedDoc]] = {}
    for d in docs:
        families.setdefault(d.title, []).append(d)

    records: list[dict] = []
    for title, members in families.items():
        members.sort(key=lambda x: parse_date(first(x.body_fields, "施行日期")) or "9999-12-31")
        ids = [m.doc_id for m in members]

        for i, d in enumerate(members):
            eff_from = parse_date(first(d.body_fields, "施行日期"))
            declared_to = parse_date(
                first(d.body_fields, "废止日期") or first(d.body_fields, "有效期至")
            )
            status = _status_from_source(d)

            # 版本链推断：后面还有新版本，则本版被替代，止于新版前一日
            superseded_by = ids[i + 1] if i + 1 < len(members) else None
            supersedes = ids[i - 1] if i > 0 else None
            effective_to = declared_to
            if superseded_by and not declared_to:
                nxt = members[i + 1]
                nxt_from = parse_date(first(nxt.body_fields, "施行日期"))
                effective_to = (date.fromisoformat(nxt_from) - timedelta(days=1)).isoformat()
                if status != "windowed":
                    status = "superseded"
            if superseded_by:
                status = "superseded"

            scope_dept, scope_region, scope_raw = _parse_scope(d)
            dep, reg, _ = scope_dept, scope_region, scope_raw

            content = "\n".join(c.text for c in d.clauses)
            need_review = d.doc_id in PENDING_CONFIRM

            records.append(
                {
                    "id": d.doc_id,
                    "title": title,
                    "version": first(d.body_fields, "版本号") or "—",
                    "source_uri": d.source_uri,
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
                    "status": status,
                    "doc_kind": _kind_from_source(d),
                    "publisher": first(d.body_fields, "发布部门"),
                    "effective_from": eff_from,
                    "effective_to": effective_to,
                    "published_at": parse_date(first(d.body_fields, "发布日期")),
                    "recorded_at": CONFIRMED_AT,
                    "supersedes_id": supersedes,
                    "superseded_by": superseded_by,
                    "scope_dept": jdump(dep),
                    "scope_region": jdump(reg),
                    "scope_grade": None,
                    "meta_confidence": 0.62 if need_review else 0.95,
                    "meta_confirmed_by": None if need_review else CONFIRMED_BY,
                    "meta_confirmed_at": None if need_review else CONFIRMED_AT,
                    "indexed_at": f"{TODAY.isoformat()}T08:00:00+08:00",
                    "_clauses": d.clauses,
                    "_need_review": need_review,
                }
            )
    return records


def _clause_id(doc_id: str, seq: int) -> str:
    return f"{doc_id}::C{seq:02d}"


TICKETS = [
    # ticket_no, applicant, name, dept, region, amount, subject, status_code, node, entered, submitted, reason, occurred
    ("QH20260812-003", "E1001", "张明", "销售部", "华东", 2480.0, "8月上海客户拜访差旅",
     "returned", "已退回待修改", "2026-09-18", "2026-08-20",
     "发票抬头与合同签约主体不一致，需重开", "2026-08-12"),
    ("QH20260915-017", "E1001", "张明", "销售部", "华东", 1860.5, "9月华东差旅",
     "finance_review", "财务复审", "2026-10-06", "2026-09-22", None, "2026-09-15"),
    ("QH20261002-008", "E1001", "张明", "销售部", "华东", 920.0, "国庆前客户拜访",
     "dept_approval", "部门负责人审批", "2026-10-08", "2026-10-08", None, "2026-10-02"),
    ("QH20260920-005", "E1002", "李静", "研发部", "华南", 3120.0, "技术大会差旅",
     "completed", "已完成付款", "2026-10-05", "2026-09-25", None, "2026-09-20"),
    ("QH20260925-011", "E1003", "王强", "销售部", "华北", 760.0, "华北出差",
     "finance_review", "财务复审", "2026-10-04", "2026-09-28", None, "2026-09-25"),
]

STATUS_LABEL = {
    "returned": "已退回",
    "finance_review": "财务复审中",
    "dept_approval": "部门审批中",
    "completed": "已完成",
}

EMPLOYEES = {
    "E1001": ("张明", "销售部", "华东"),
    "E1002": ("李静", "研发部", "华南"),
    "E1003": ("王强", "销售部", "华北"),
}


def seed(reset: bool = True, db_path: Path | str | None = None) -> None:
    import sqlite3

    from .db import DB_PATH

    conn = connect(db_path or DB_PATH)
    init_db(conn, reset=reset)

    docs = load_all()
    records = build_documents(docs)

    for r in records:
        clauses = r.pop("_clauses")
        need_review = r.pop("_need_review")
        conn.execute(
            """INSERT INTO policy_document
               (id,title,version,source_uri,content_hash,status,doc_kind,publisher,
                effective_from,effective_to,published_at,recorded_at,
                scope_dept,scope_region,scope_grade,
                meta_confidence,meta_confirmed_by,meta_confirmed_at,indexed_at)
               VALUES (:id,:title,:version,:source_uri,:content_hash,:status,:doc_kind,:publisher,
                :effective_from,:effective_to,:published_at,:recorded_at,
                :scope_dept,:scope_region,:scope_grade,
                :meta_confidence,:meta_confirmed_by,:meta_confirmed_at,:indexed_at)""",
            r,
        )
        # 版本链不在这里写，见下方第二阶段统一回填
        for c in clauses:
            # text 必须是原文，禁止改写或摘要
            conn.execute(
                "INSERT INTO policy_clause (id,document_id,clause_no,heading,text,seq)"
                " VALUES (?,?,?,?,?,?)",
                (_clause_id(r["id"], c.seq), r["id"], c.clause_no, c.heading, c.text, c.seq),
            )

        # 未经确认的新版本 → 生成待确认项，进乔姐工作台
        if need_review:
            conn.execute(
                """INSERT INTO metadata_review_queue
                   (id,doc_id,kind,proposal,evidence,confidence,status,resolved_by,resolved_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    f"RV-{r['id']}",
                    r["id"],
                    "new_version",
                    jdump(
                        {
                            "version": r["version"],
                            "effective_from": r["effective_from"],
                            "supersedes_id": r["supersedes_id"],
                        }
                    ),
                    "源文档头部标注「施行日期：2026 年 10 月 1 日」，"
                    "且与同家族《市内交通费管理办法》v1 形成版本关系",
                    0.62,
                    "pending",
                    None,
                    None,
                ),
            )

    # ── 第二阶段：版本链统一回填（自引用外键要求目标行已存在） ──
    for r in records:
        conn.execute(
            "UPDATE policy_document SET supersedes_id=:a, superseded_by=:b WHERE id=:id",
            {"a": r["supersedes_id"], "b": r["superseded_by"], "id": r["id"]},
        )
    conn.commit()

    for t in TICKETS:
        conn.execute(
            """INSERT INTO erp_expense_ticket
               (ticket_no,applicant_id,applicant_name,dept,region,amount,subject,
                status_code,status_label,current_node,node_entered_at,submitted_at,
                last_action_at,last_return_reason,occurred_on)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7],
                STATUS_LABEL[t[7]], t[8], t[9], t[10],
                t[9], t[11], t[12],
            ),
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    seed()
    print(f"数据库已重建：{Path('data/clause.db').resolve()}")
