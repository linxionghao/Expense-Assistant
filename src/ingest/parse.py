"""
ingest/parse.py —— 从乔姐维护的源文档解析出条款层。

约束（对应《落地细节.md》§1.2）：
  clause.text 必须是原文，禁止改写或摘要。摘要过的条款不能作为引用依据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"

# 「第 3 条 住宿费标准」/「第 3.2 条 xxx」/「第一条 xxx」
_CLAUSE_RE = re.compile(r"^#{2,3}\s*第\s*([0-9一二三四五六七八九十]+(?:\.[0-9]+)?)\s*条\s*(.*)$")

_CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def cn_to_int(s: str) -> int:
    """「三」→3，「十」→10，「十二」→12。够用即可，不追求完备。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    total = 0
    for ch in s:
        total += _CN_NUM.get(ch, 0)
    # 「十」「十一」的简单修正
    if s.startswith("十") and len(s) > 1:
        total += 10 - _CN_NUM.get(s[1], 0) + _CN_NUM.get(s[1], 0)
    return total if total else 0


@dataclass
class ParsedClause:
    clause_no: str
    heading: str
    text: str
    seq: int


@dataclass
class ParsedDoc:
    doc_id: str
    title: str
    source_uri: str
    body_fields: dict = field(default_factory=dict)
    clauses: list[ParsedClause] = field(default_factory=list)


def parse_markdown(path: Path) -> ParsedDoc:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), path.stem)
    body_fields: dict[str, list[str]] = {}
    clauses: list[ParsedClause] = []

    cur: ParsedClause | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal cur, buf
        if cur is not None:
            text = "\n".join(buf).strip()
            if text:
                cur.text = text
                clauses.append(cur)
        cur, buf = None, []

    for ln in lines:
        heading = _CLAUSE_RE.match(ln.strip())
        if heading:
            flush()
            no, head = heading.group(1), heading.group(2).strip()
            cur = ParsedClause(
                clause_no=f"第 {no} 条",
                heading=head,
                text="",
                seq=len(clauses),
            )
            continue

        stripped = ln.strip()

        # 头部元信息：「**版本号**：v2」
        kv = re.match(r"^\*\*(.+?)\*\*\s*[:：]\s*(.*)$", stripped)
        if kv and cur is None:
            key = kv.group(1).strip()
            val = kv.group(2).strip()
            body_fields.setdefault(key, []).append(val)
            continue

        if stripped.startswith(">"):  # 说明性引注不进条款正文
            continue
        if stripped.startswith("# "):
            continue

        if cur is not None:
            buf.append(stripped)

    flush()
    for c in clauses:
        c.seq = clauses.index(c)

    return ParsedDoc(
        doc_id=path.stem,
        title=title,
        source_uri=f"file:///{path.as_posix()}",
        body_fields=body_fields,
        clauses=clauses,
    )


def load_all(dirpath: Path = POLICY_DIR) -> list[ParsedDoc]:
    docs = [parse_markdown(p) for p in sorted(dirpath.glob("*.md"))]
    for d in docs:
        for i, c in enumerate(d.clauses):
            c.seq = i
    return docs


def first(body_fields: dict, key: str) -> str | None:
    vals = body_fields.get(key)
    return vals[0] if vals else None


DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse_date(s: str | None) -> str | None:
    """把中文日期或 ISO 日期统一成 YYYY-MM-DD。"""
    if not s:
        return None
    m = ISO_RE.search(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = DATE_RE.search(s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None
