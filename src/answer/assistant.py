"""
answer/assistant.py —— 意图分类 + 三岔口路由 + 回答组装。

《设计方案.md》§6：这类系统的成败点不是"答错"，而是"该说不知道的时候硬答了一个"。

    answer    命中唯一、版本明确、无冲突      → 给答案 + 强制引用卡片
    escalate  多版本/歧义/例外/制度无明确规定 → 给原文 + 一键转交乔姐或周敏
    refuse    越界请求（查他人、写操作）      → 明确拒绝并说明为什么

兜底原则：宁可说"我不确定，请找乔姐/周敏"，也绝不编一句"应该可以报"。
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from ..auth.gateway import Identity, audit, format_ticket_state, query_my_tickets
from ..db import rows
from ..retrieval.search import (
    TODAY,
    detect_conflict,
    parse_as_of,
    pending_docs,
    search,
    supersession_notice,
)

DISCLAIMER = (
    "本助手内容以乔姐维护的现行制度为准，单据状态以报销系统记录为准，不构成报销承诺。"
)

# ── 意图识别（规则优先，确定性可控；LLM 只做可选的表达层） ──────────

WRITE_INTENT = ["改成", "修改", "帮我提交", "代我提交", "帮我催", "催一下", "删掉", "撤销"]

OTHERS_RE = [
    "张三", "李四", "王五", "别人的", "其他人的", "同事的", "别人的单", "老板的单",
    "所有人", "全公司", "其他人",
]

TICKET_INTENT = [
    "我的单", "我那笔", "我那单", "我这笔", "到哪了", "到哪", "到哪一步", "进度",
    "被退", "退回了", "为什么退", "通过了吗", "审批到", "还要多久", "到账", "报销单状态",
    "状态怎么样", "批了吗", "好了吗",
]

PREDICT_INTENT = ["快通过", "能通过吗", "应该可以", "会不会退", "能过吗", "有希望吗"]

ESCALATE_HINT = [
    "能报多少", "可以报吗", "能不能报", "给报销吗", "算不算", "特殊情况", "例外",
    "这个是特殊情况", "领导口头同意", "没票", "没有发票",
]


def has_any(q: str, words: list[str]) -> list[str]:
    return [w for w in words if w in q]


@dataclass
class Citation:
    label: str
    period: str
    source_uri: str
    text: str
    role: str = "primary"          # primary | superseding | overlay
    note: str = ""


@dataclass
class Answer:
    mode: str                       # answer | escalate | refuse
    headline: str = ""
    body: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    trace: dict = field(default_factory=dict)

    @property
    def mode_label(self) -> str:
        return {"answer": "回答", "escalate": "转交", "refuse": "拒绝"}[self.mode]


# ──────────────────────────────────────────────────────────────
# 越界拦截
# ──────────────────────────────────────────────────────────────

def mentioned_other_people(conn: sqlite3.Connection, q: str, identity: Identity) -> list[str]:
    """
    检测问题里是否提到了**别人**。

    不用硬编码人名黑名单（那必然漏），而是查业务系统花名册：
    任何出现在问题里、且不等于当前登录人的姓名，都视为他人指代。

    真实环境里花名册来自 HR/通讯录接口，这里从申请人名录取。
    """
    rs = conn.execute("SELECT DISTINCT applicant_name FROM erp_expense_ticket").fetchall()
    return [r["applicant_name"] for r in rs
            if r["applicant_name"] and r["applicant_name"] in q
            and r["applicant_name"] != identity.name]


def check_refuse(conn, q: str, identity: Identity) -> Answer | None:
    writes = has_any(q, WRITE_INTENT)
    if writes:
        return _refuse(
            conn, identity, q,
            reason=f"助手对报销单据没有任何写权限，无法执行「{writes[0]}」。",
            why="一旦助手能改单子，周敏的核对责任就会悄悄转移到助手身上，"
                "出问题时责任链会断掉。因此本助手设计为只读。"
                "单据内容的更正请在报销系统内自行操作。",
        )

    named_others = mentioned_other_people(conn, q, identity)
    lexical_others = has_any(q, OTHERS_RE)
    ticketish = (has_any(q, TICKET_INTENT) or has_any(q, PREDICT_INTENT)
                 or has_any(q, ["报销了多少", "报了多少", "金额是多少", "的单"]))

    if ticketish and (named_others or lexical_others):
        who_word = named_others[0] if named_others else lexical_others[0]
        target = who_word if named_others else "他人单据"
        return _refuse(
            conn, identity, q,
            reason=f"只能查询本人作为申请人的单据；提问中涉及「{who_word}」，超出你的授权范围。",
            why="身份由企业微信通道提供，在系统侧强制行级过滤，"
                "不接受通过对话更换身份，也不接受「请输入工号」这种自报身份。"
                "因此即便在对话里提到别人，也拿不到别人的数据。",
            target=target,
        )
    return None


def _refuse(conn, identity: Identity, q: str, *, reason: str, why: str,
            target: str = "") -> Answer:
    audit(conn, identity, intent="refused", query_text=q, target=target,
          result_code="denied", detail=reason)
    return Answer(
        mode="refuse",
        headline="这个我做不了。",
        body=[reason, why],
        actions=["如确需协助，请联系周敏或乔姐"],
        trace={"intent": "越界请求", "result_code": "denied"},
    )


# ──────────────────────────────────────────────────────────────
# 单据类：只读转述
# ──────────────────────────────────────────────────────────────

def answer_ticket(conn, q: str, identity: Identity) -> Answer:
    tickets = query_my_tickets(identity, conn=conn)
    if not tickets:
        return Answer(
            mode="escalate",
            headline="没有查到你名下的待处理单据。",
            body=["业务系统里没有返回属于你的单据记录。如果确实提交过，请找周敏核对数据是否同步。"],
            actions=["转交周敏核对"],
            trace={"intent": "单据查询", "result_code": "not_found"},
        )

    # 「我上个月那笔」/「我这个月那笔」→ 取最近一条；命中具体单号则精确匹配
    no = re.search(r"QH\d{8}-\d{3}", q)
    picked = tickets
    if no:
        picked = [t for t in tickets if t["ticket_no"] == no.group(0)]
        if not picked:
            return Answer(
                mode="escalate",
                headline=f"未查到单据 {no.group(0)}。",
                body=["该单号不属于你，或业务系统中不存在。单据状态以报销系统记录为准，请找周敏核对。"],
                actions=["转交周敏核对"],
                trace={"intent": "单据查询", "result_code": "not_found"},
            )
    elif has_any(q, ["被退", "退回了", "为什么退", "退回原因"]):
        ret = [t for t in tickets if t["status_code"] == "returned"]
        if ret:
            picked = ret

    body = [format_ticket_state(t) for t in picked]

    predict = has_any(q, PREDICT_INTENT)
    notices = []
    if predict:
        notices.append(
            "审批结论由周敏核对后作出，助手不对结果做任何预测或推断。"
        )
    notices.append("以上为报销系统记录的字段原样转述，不含金额、事由等敏感字段。")

    audit(conn, identity, intent="ticket_query", query_text=q,
          target=",".join(t["ticket_no"] for t in picked), result_code="ok",
          detail=f"{len(picked)} rows")

    return Answer(
        mode="answer",
        headline=f"查到 {len(picked)} 张你名下的单据：",
        body=body,
        notices=notices,
        actions=["转交周敏（如对状态有疑问）"],
        trace={"intent": "单据查询", "matched": [t["ticket_no"] for t in picked]},
    )


# ──────────────────────────────────────────────────────────────
# 制度类：检索 + 引用 + 版本提示 + 冲突降级
# ──────────────────────────────────────────────────────────────

def answer_policy(conn, q: str, identity: Identity) -> Answer:
    as_of, as_of_trace = parse_as_of(q)
    hits, meta = search(conn, q, as_of=as_of, dept=identity.dept,
                        region=identity.region, top_k=5)

    trace = {
        "intent": "制度查询",
        "as_of": as_of.isoformat(),
        "as_of_trace": as_of_trace,
        "candidate_pool": meta["candidate_count"],
        "expanded_terms": meta["expanded_terms"],
        "gated_out_unconfirmed": _unconfirmed_at(conn, as_of),
    }

    # 制度里没有写明的事，不许推测
    if not hits:
        return Answer(
            mode="escalate",
            headline="在现行制度里没有找到可靠依据。",
            body=[
                f"已按时间锚点 {as_of.isoformat()} 在你适用范围内的生效条款里检索，没有命中。",
                "制度没有明确规定的事项，助手不做推测——因为报销场景下猜错的成本由你来承担。",
            ],
            actions=["转交乔姐确认", "查看相关制度原文"],
            trace=trace,
        )

    top = hits[0]
    conflict = detect_conflict(conn, hits)

    # 冲突 → 不做裁量，降级为转交
    if conflict and conflict["type"] == "conflict":
        return _conflict_escalate(conn, identity, q, hits, conflict, trace)

    cites = [Citation(
        label=top.label,
        period=top.period,
        source_uri=top.source_uri,
        text=top.text,
        role="primary",
    )]

    notices: list[str] = []

    # ★ 硬性：只要 as_of 落在已废止版本区间，必须提示现行版本
    sup = supersession_notice(conn, top)
    if sup:
        cites.append(Citation(
            label=f"{sup['latest_doc']} · {sup['latest_clause_no'] or '全文'}",
            period=f"{sup['latest_effective_from']} 起施行",
            source_uri=_source_of(conn, sup["latest_doc"].split()[-1]),
            text=sup["latest_text"] or "",
            role="superseding",
            note="现行版本",
        ))
        notices.append(
            f"你问的这条来自《{top.title} {top.version}》，"
            f"该版本有效期为 {top.period}，现行版本已是 {sup['latest_doc']}"
            f"（{sup['latest_effective_from']} 起施行）。"
            f"如果这笔费用尚未发生，请以现行版本为准；"
            f"如果费用确实发生在 {as_of.isoformat()}，则适用上面引用的旧版标准。"
        )

    # 多条规定并存：同样是"不止一条"，处理方式和上面完全不同
    if conflict and conflict["type"] == "overlay":
        ev = conflict["hit"]
        cites.append(Citation(
            label=f"{ev.title} · {ev.clause_no}",
            period=ev.period,
            source_uri=ev.source_uri,
            text=ev.text,
            role="overlay",
            note="专项规定/限时规定，在你的适用范围内同时生效",
        ))
        notices.append(f"注意：{conflict['detail']}。请一并核对，或转交乔姐确认两者的适用关系。")

    # 同一制度家族存在乔姐尚未确认的版本 → 显式告知，不可静默
    pend = pending_docs(conn, top.title)
    if pend:
        p = pend[0]
        notices.append(
            f"提示：检测到《{p['title']}》疑似有 {p['version']}（施行日期 {p['effective_from']}），"
            f"但其元数据尚未经制度维护人乔姐确认，因此本次回答不采用该版本。"
            f"在乔姐确认之前，请以现行已知版本为准。"
        )

    if status_is_repealed(top):
        notices.append(f"注意：你引用的这份制度本身已不在有效期内（{top.period}）。")

    # 多选一的具体情况判断 → 交给周敏，不裁量
    if has_any(q, ESCALATE_HINT) and _needs_fact_judgement(q):
        return Answer(
            mode="escalate",
            headline="这个问题需要结合具体事实判断，助手不替你下结论。",
            body=[
                "制度规定是通用的，但你的这笔**能不能报、报多少**，取决于发票、事由、"
                "审批链等具体事实的核对。业务事实的最终核对责任在周敏。",
                "下面是相关的制度原文，你可以先自行核对：",
            ],
            citations=cites,
            notices=notices,
            actions=["转交周敏核对", "转交乔姐解释条款"],
            trace=trace,
        )

    notice_texts = notices + [DISCLAIMER]
    audit(conn, identity, intent="policy_query", query_text=q,
          target=top.clause_id, result_code="ok", as_of=as_of.isoformat(),
          detail=top.label)

    return Answer(
        mode="answer",
        headline=f"按 {as_of.isoformat()} 当时有效的制度，相关条款如下：",
        body=[f"{top.heading}："],
        citations=cites,
        notices=notice_texts,
        actions=["转交乔姐确认", "复制引用"],
        trace=trace,
    )


def _needs_fact_judgement(q: str) -> bool:
    return bool(has_any(q, ["能报多少", "可以报吗", "能不能报", "这笔", "这单", "这次"]))


def status_is_repealed(hit) -> bool:
    return hit.status == "superseded" and not hit.superseded_by


def _source_of(conn, version: str) -> str:
    r = conn.execute(
        "SELECT source_uri FROM policy_document WHERE version=? LIMIT 1", (version,)
    ).fetchone()
    return r["source_uri"] if r else ""


def _unconfirmed_at(conn, as_of: date) -> list[dict]:
    """因为乔姐未确认而被硬闸门挡在候选集外的文档 —— 要能在界面上解释清楚。"""
    rs = rows(
        conn,
        """SELECT title,version,effective_from FROM policy_document
           WHERE meta_confirmed_by IS NULL
             AND date(effective_from) <= date(:d)
             AND (effective_to IS NULL OR date(effective_to) >= date(:d))""",
        {"d": as_of.isoformat()},
    )
    return [dict(r) for r in rs]


def _conflict_escalate(conn, identity, q, hits, conflict, trace) -> Answer:
    top, other = hits[0], None
    for h in hits[1:]:
        if h.label == conflict["b"]:
            other = h
            break
    cites = [
        Citation(label=top.label, period=top.period, source_uri=top.source_uri,
                 text=top.text, role="primary"),
    ]
    if other:
        cites.append(Citation(label=other.label, period=other.period,
                              source_uri=other.source_uri, text=other.text,
                              role="overlay", note="与之并存但标准不一致"))

    audit(conn, identity, intent="policy_query", query_text=q,
          target=top.clause_id, result_code="escalated",
          detail="检测到同期有效的同一事项规定不一致，降级为转交")

    return Answer(
        mode="escalate",
        headline="同一时间有两条规定并存且标准不一致，助手不做选择。",
        body=[
            f"按 {trace.get('as_of')} 检索，以下两份文件同时有效，且对同一事项的规定不一致：",
            conflict["detail"],
            "在这种情形下替你挑一条，等于让助手替公司做了制度解释。制度解释权在乔姐。",
        ],
        citations=cites,
        notices=[DISCLAIMER],
        actions=["转交乔姐裁定适用关系"],
        trace=trace,
    )


# ──────────────────────────────────────────────────────────────
# 统一入口
# ──────────────────────────────────────────────────────────────

def ask(conn, question: str, identity: Identity) -> Answer:
    question = question.strip()

    refused = check_refuse(conn, question, identity)
    if refused:
        return refused

    # 「这笔应该快通过了吧」这类预测句指代的是单据，必须走单据路由，
    # 否则会被当成制度问题答成一堆无关条款 —— 那是典型的答非所问
    if has_any(question, TICKET_INTENT) or has_any(question, PREDICT_INTENT):
        return answer_ticket(conn, question, identity)

    return answer_policy(conn, question, identity)
