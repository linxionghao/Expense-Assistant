"""
retrieval/search.py —— 制度检索管线。

对参考项目 temporal-rag 的核心改动：
  它把时效性做成「重排加权」，旧版本文档仍然留在候选上下文里（会被 LLM 缝合）。
  这里改成 **as_of 硬闸**：不在 as_of 当时生效的条款，SQL 层直接过滤掉，
  根本不进入候选集；命中旧版本时强制附加现行版本提示。

流程（《落地细节.md》§2）：
  as_of 解析 → 元数据硬过滤 → 混合检索(BM25) → 版本/冲突检测 → 组装候选
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..db import connect, jload, rows

TODAY = date(2026, 10, 9)

# ── 术语归一化（报销场景必备，对应《落地细节.md》§2 术语表） ──────────
# 员工口语 → 制度术语。这笔emo演示「打车能报吗」能命中《市内交通费管理办法》
TERM_MAP: dict[str, list[str]] = {
    "打车": ["市内交通费", "交通费"],
    "滴滴": ["市内交通费", "交通费"],
    "网约车": ["市内交通费", "交通费"],
    "出租车": ["市内交通费", "交通费"],
    "请客": ["业务招待费", "招待费"],
    "宴请": ["业务招待费", "招待费"],
    "招待": ["业务招待费", "招待费"],
    "吃饭": ["业务招待费", "伙食补助"],
    "住宿": ["住宿费"],
    "酒店": ["住宿费"],
    "宾馆": ["住宿费"],
    "发票丢": ["发票丢失"],
    "丢了发票": ["发票丢失"],
    "机票": ["城市间交通费", "交通费"],
    "高铁": ["城市间交通费", "交通费"],
    "火车票": ["城市间交通费", "交通费"],
    "差补": ["伙食补助"],
    "补助": ["伙食补助"],
    "贴票": ["报销", "票据"],
    "专票": ["增值税专用发票"],
    "报销标准": ["标准"],
}


def expand_terms(q: str) -> list[str]:
    """把口语词扩展成制度术语，一起参与检索。"""
    out = []
    for k, vs in TERM_MAP.items():
        if k in q:
            out.extend(vs)
    return out


_CN = re.compile(r"[\u4e00-\u9fff]")
_ALNUM = re.compile(r"[a-zA-Z]+|\d+")


def tokenize(text: str) -> list[str]:
    """中文按字 + bigram，英文数字按词。无分词器依赖。"""
    chars = _CN.findall(text)
    toks = list(chars)
    toks += [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    toks += _ALNUM.findall(text)
    return toks


# ──────────────────────────────────────────────────────────────
# as_of 解析
# ──────────────────────────────────────────────────────────────

_MONTH_DAY = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_FULL_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_REL_MONTH = re.compile(r"(上上个?月|上个月|本月|这个月|上月)")


def parse_as_of(q: str, today: date = TODAY) -> tuple[date, str]:
    """
    优先级：《设计方案.md》§2.1
      用户明确说的费用发生日 > 单据发生日 > 今天
    """
    m = _FULL_DATE.search(q)
    if m:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d, f"问题中明确指出费用发生日 {d.isoformat()}"
    m = _MONTH_DAY.search(q)
    if m:
        mo, dd = int(m.group(1)), int(m.group(2))
        y = today.year
        cand = date(y, mo, dd)
        if cand > today:          # 「7月12号」在今天之后 → 指去年
            cand = date(y - 1, mo, dd)
        return cand, f"问题中提到的 {mo} 月 {dd} 日 → 按 {cand.isoformat()} 检索"
    m = _REL_MONTH.search(q)
    if m:
        word = m.group(1)
        if "上上" in word:
            d = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            d = (d - timedelta(days=1)).replace(day=min(dd := dd_or_15(today), 28))
            return d, f"「{word}」→ 按 {d.isoformat()} 检索"
        if word in ("上个月", "上月"):
            d = (today.replace(day=1) - timedelta(days=1))
            d = d.replace(day=min(dd_or_15(today), d.day))
            return d, f"「{word}」→ 按 {d.isoformat()} 检索"
        return today, f"「{word}」→ 按今天 {today.isoformat()} 检索"
    return today, f"问题未指明时间 → 按今天 {today.isoformat()} 检索"


def dd_or_15(t: date) -> int:
    return min(t.day, 28)


# ──────────────────────────────────────────────────────────────
# 候选集：元数据硬过滤
# ──────────────────────────────────────────────────────────────

@dataclass
class Hit:
    clause_id: str
    document_id: str
    clause_no: str
    heading: str
    text: str
    title: str
    version: str
    source_uri: str
    status: str
    doc_kind: str
    publisher: str | None
    effective_from: str
    effective_to: str | None
    superseded_by: str | None
    scope_dept: list | None
    scope_region: list | None
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def period(self) -> str:
        to = self.effective_to or "至今"
        return f"{self.effective_from} ~ {to}"

    @property
    def label(self) -> str:
        return f"{self.title} {self.version} · {self.clause_no}"


def _clause_row(r) -> Hit:
    return Hit(
        clause_id=r["id"],
        document_id=r["document_id"],
        clause_no=r["clause_no"],
        heading=r["heading"],
        text=r["text"],
        title=r["title"],
        version=r["version"],
        source_uri=r["source_uri"],
        status=r["status"],
        doc_kind=r["doc_kind"],
        publisher=r["publisher"],
        effective_from=r["effective_from"],
        effective_to=r["effective_to"],
        superseded_by=r["superseded_by"],
        scope_dept=jload(r["scope_dept"]),
        scope_region=jload(r["scope_region"]),
    )


CANDIDATE_SQL = """
SELECT c.id, c.document_id, c.clause_no, c.heading, c.text, c.seq,
       d.title, d.version, d.source_uri, d.status, d.doc_kind, d.publisher,
       d.effective_from, d.effective_to, d.superseded_by, d.scope_dept, d.scope_region
FROM policy_clause c
JOIN policy_document d ON d.id = c.document_id
WHERE date(d.effective_from) <= date(:as_of)
  AND (d.effective_to IS NULL OR date(d.effective_to) >= date(:as_of))
  AND d.meta_confirmed_by IS NOT NULL          -- ★ 未确认的不得用于回答
  AND d.status IN ('effective','superseded','windowed')
"""


def candidates(conn, as_of: date, dept: str | None = None,
               region: str | None = None) -> list[Hit]:
    rs = rows(conn, CANDIDATE_SQL, {"as_of": as_of.isoformat()})
    hits = [_clause_row(r) for r in rs]

    out = []
    for h in hits:
        # 适用范围命中后过滤
        if h.scope_dept and not (dept and dept in h.scope_dept):
            continue
        if h.scope_region and not (region and region in h.scope_region):
            continue
        out.append(h)
    return out


# ──────────────────────────────────────────────────────────────
# BM25
# ──────────────────────────────────────────────────────────────

class BM25:
    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.corpus = corpus
        self.k1, self.b = k1, b
        self.N = len(corpus)
        self.dl = [len(d) for d in corpus]
        self.avgdl = sum(self.dl) / self.N if self.N else 0.0
        self.tf = [Counter(d) for d in corpus]
        self.df: Counter = Counter()
        for d in self.tf:
            for t in d:
                self.df[t] += 1
        if self.N:
            self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in self.df.items()}
        else:
            self.idf = {}

    def scores(self, query_tokens: list[str]) -> list[float]:
        out = []
        for i in range(self.N):
            s = 0.0
            for t in query_tokens:
                f = self.tf[i].get(t, 0)
                if not f:
                    continue
                idf = self.idf.get(t)
                if idf is None:
                    continue
                dl = self.dl[i] or 1
                s += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
            out.append(s)
        return out


def _doc_theme_boost(h: Hit, expanded: list[str]) -> float:
    """
    专项制度优先于通用制度。

    「打车能报吗」应该优先引《市内交通费管理办法》（整份文档都管这件事），
    而不是《差旅费管理办法》里附带提及市内交通费的某一小条。
    判断依据：扩展出来的制度术语是否就是这部文档的标题主题。
    """
    for term in expanded:
        if term and term in h.title:
            return 1.45
    return 1.0


def search(conn, question: str, *, as_of: date | None = None, dept=None, region=None,
           top_k: int = 5, explain: bool = False) -> tuple[list[Hit], dict]:
    as_of_trace = None
    if as_of is None:
        as_of, as_of_trace = parse_as_of(question)
    cands = candidates(conn, as_of, dept, region)
    if not cands:
        return [], {"as_of": as_of, "candidate_count": 0, "note": "硬闸门过滤后无候选"}

    # 检索文本 = 条款标题 + 原文（标题权重靠重复实现）
    corpus = [tokenize(h.heading * 2 + " " + h.heading + " " + h.text) for h in cands]
    bm = BM25(corpus)

    q_tokens = tokenize(question)
    expanded = expand_terms(question)
    boost_tokens = []
    for t in expanded:
        boost_tokens.extend(tokenize(t))

    base = bm.scores(q_tokens)
    boosted = bm.scores(boost_tokens) if boost_tokens else [0.0] * len(cands)

    q_union = set(q_tokens) | set(boost_tokens)
    for i, h in enumerate(cands):
        # 条款标题与问句的表层共现：问「打车能报吗」时，
        # 「据实报销原则」应压过「包干试点」——后者 TF-IDF 分数高但答非所问
        head_hit = len(set(tokenize(h.heading)) & q_union)
        h.score = base[i] + 1.4 * boosted[i] + 1.2 * head_hit
        # 专项制度整体加权放在最后，避免被通用制度的同名小条款压过
        if _doc_theme_boost(h, expanded) > 1.0:
            h.score *= 2.0
        if boosted[i] > 0:
            h.reasons.append("术语归一化命中")
        if _doc_theme_boost(h, expanded) > 1.0:
            h.reasons.append("专项制度优先")
        if head_hit:
            h.reasons.append(f"条款标题相关({head_hit})")

    cands.sort(key=lambda h: h.score, reverse=True)
    top = [h for h in cands if h.score > 0][:top_k]

    meta = {
        "as_of": as_of.isoformat(),
        "as_of_trace": as_of_trace or "调用方显式指定",
        "candidate_count": len(cands),
        "expanded_terms": expanded,
    }
    if explain:
        meta["pool"] = [
            {"label": h.label, "score": round(h.score, 3)} for h in cands if h.score > 0
        ][:12]
    return top, meta


# ──────────────────────────────────────────────────────────────
# 版本链 / 冲突检测
# ──────────────────────────────────────────────────────────────

def current_version_of(conn, doc_id: str) -> str | None:
    """顺着 superseded_by 找到家族里现行有效的最新版本。"""
    seen = set()
    cur = doc_id
    while cur and cur not in seen:
        seen.add(cur)
        r = conn.execute(
            "SELECT superseded_by, status FROM policy_document WHERE id=?", (cur,)
        ).fetchone()
        if not r or not r["superseded_by"]:
            return cur
        cur = r["superseded_by"]
    return None


def supersession_notice(conn, hit: Hit) -> dict | None:
    """
    强制版本提示（《落地细节.md》§2）：
    只要命中的是已被替代的旧版本，就必须提示现行版本，否则员工会拿旧标准去报销。
    """
    if not hit.superseded_by:
        return None
    latest = current_version_of(conn, hit.document_id)
    if not latest or latest == hit.document_id:
        return None
    r = conn.execute(
        "SELECT title, version, effective_from, status FROM policy_document WHERE id=?",
        (latest,),
    ).fetchone()
    if not r:
        return None
    # 在该版本里找同标题条款，给出可跳转的精确位置
    same = conn.execute(
        "SELECT clause_no, text FROM policy_clause WHERE document_id=? AND heading=?",
        (latest, hit.heading),
    ).fetchone()
    return {
        "latest_doc": f"{r['title']} {r['version']}",
        "latest_effective_from": r["effective_from"],
        "latest_clause_no": same["clause_no"] if same else None,
        "latest_text": same["text"] if same else None,
    }


def _scoped(h: Hit) -> bool:
    return bool(h.scope_dept or h.scope_region)


def detect_conflict(conn, hits: list[Hit]) -> dict | None:
    """
    区分两种「不止一条规定」，这是能否给出答案的分水岭：

      conflict  同一适用范围里有两条内容打架的规定
                → 助手不做裁量，降级转交（《设计方案.md》§6）
      overlay   专项规定对**这位员工**的适用范围叠加/临时窗口叠加
                → 不阻断回答，但必须把两条都摆出来并说明关系

    差别在于 scope：一条是公司级通适规定，另一条是部门/地区专项规定且当前
    用户确实在其适用范围内时，这不是冲突，是制度的正常分层。
    """
    if len(hits) < 2:
        return None
    top = hits[0]
    others = [h for h in hits[1:] if h.score > top.score * 0.55]

    for h in others:
        if not (set(tokenize(top.heading)) & set(tokenize(h.heading))):
            continue
        a, b = extract_amounts(top.text), extract_amounts(h.text)
        if a and b and a != b:
            if _scoped(top) != _scoped(h):
                # 一个是通适规定，一个是专项规定，且都命中了这位员工
                specialist = h if _scoped(h) else top
                general = top if specialist is h else h
                return {
                    "type": "overlay",
                    "hit": specialist,
                    "detail": f"《{general.title} {general.version}》是公司级通适规定，"
                              f"《{specialist.title}》是针对「{specialist.scope_dept or ''}"
                              f"{'' if not specialist.scope_region else '/' + '/'.join(specialist.scope_region)}」"
                              f"的专项补充规定，在你的适用范围内同时生效。",
                }
            return {
                "type": "conflict",
                "a": top.label, "b": h.label,
                "detail": f"《{top.title} {top.version}》与《{h.title} {h.version}》"
                          f"适用范围相同，但在同一时间的规定不一致",
            }

    # 限时有效叠加（EVENT）不算冲突，但要提示
    events = [h for h in hits if h.doc_kind == "EVENT" and h.score > top.score * 0.4]
    if events:
        return {
            "type": "overlay",
            "detail": f"另有临时文件在窗口期内叠加适用：《{events[0].title}》"
                      f"（{events[0].period}）",
            "hit": events[0],
        }
    return None


def extract_amounts(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*元", text)]


def pending_docs(conn, family_title: str) -> list[dict]:
    """该制度家族下是否存在乔姐尚未确认的版本（要显式提示员工）。"""
    rs = rows(
        conn,
        """SELECT id,title,version,effective_from FROM policy_document
           WHERE title=? AND meta_confirmed_by IS NULL""",
        (family_title,),
    )
    return [dict(r) for r in rs]


if __name__ == "__main__":
    conn = connect()
    for q in ["出差住宿标准多少", "我7月12号出差的住宿标准多少", "打车能报吗"]:
        hits, meta = search(conn, q)
        print(f"\n【{q}】as_of={meta['as_of']} 候选池={meta['candidate_count']}")
        for h in hits[:3]:
            print(f"   {h.score:6.2f}  {h.label}  ({h.period})")
