"""
run_demo.py —— 命令行演示。

演示顺序按《落地细节.md》§6，重点不是"它多聪明"，而是"它多有分寸"：
  1  现行制度问题      → 带引用的答案
  2  同一个问题换日期  → 返回当时有效的那一版 + 现行版本提示  ★ 最能打
  3  需要裁量的问题    → 不给结论，转交                    ★ 第二能打
  4  越权：A 查 B 的单 → 兜住，并展示审计里的 denied       ★ 第三
  5  写操作            → 拒绝
  6  乔姐的待确认队列  → 助手提议、乔姐批准
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.answer.assistant import DISCLAIMER, ask
from src.auth.gateway import Identity, audit_trail, query_my_tickets, resolve_identity
from src.db import connect, rows
from src.seed import seed

LINE = "─" * 78
THICK = "═" * 78

C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "cyan": "\033[36m", "yellow": "\033[33m", "green": "\033[32m",
    "red": "\033[31m", "blue": "\033[34m",
}


def c(s: str, *styles: str) -> str:
    pre = "".join(C[x] for x in styles)
    return f"{pre}{s}{C['reset']}"


def scene(no: str, title: str, why: str) -> None:
    print(f"\n{THICK}")
    print(c(f" 场景 {no}  {title}", "bold", "cyan"))
    print(c(f" {why}", "dim"))
    print(THICK)


def render(a, show_trace: bool = False) -> None:
    badge = {
        "answer": c("［回答］", "green") ,
        "escalate": c("［转交］", "yellow"),
        "refuse": c("［拒绝］", "red"),
    }[a.mode]
    print(f"\n{badge} {c(a.headline, 'bold')}")
    for b in a.body:
        print(f"   {b}")

    for cite in a.citations:
        role = {
            "primary": "主引用",
            "superseding": "现行版本",
            "overlay": "叠加/并存",
        }[cite.role]
        tag = c(f"[{role}]", "blue")
        print(f"\n   {tag} {c(cite.label, 'bold')}")
        print(f"          生效期：{cite.period}")
        print(f"          来源：{cite.source_uri}")
        for line in cite.text.splitlines():
            print(f"          │ {line}")

    for n in a.notices:
        mark = "※" if n != DISCLAIMER else "◎"
        print(f"\n   {mark} {n}")

    if a.actions:
        print(f"\n   可执行： {' | '.join(a.actions)}")

    if show_trace and a.trace:
        print(f"\n   {c('决策轨迹', 'dim')}：")
        for k, v in a.trace.items():
            print(f"     · {k} = {v}")


def main() -> None:
    print(c("\n青禾公司 · 报销助手 Demo", "bold"))
    print(c("定位：制度检索窗口 + 本人单据状态的授权只读窗口。不生产规则，也不裁定事实。\n", "dim"))
    print(f"今天：2026-10-09   ｜ 制度维护：乔姐   ｜ 业务事实核对：周敏")

    seed()
    conn = connect()

    # ═══════════════════════════════════════════════════════════
    scene("1", "问一个现行有效的制度问题",
          "验证：答案必须带可核对的引用锚点，员工能一眼看出引的是哪一条")

    who = resolve_identity("wm_zhangming")
    print(f"\n{c('张明（销售部 / 华东）', 'dim')} 问：{c('出差住宿标准多少？', 'bold')}")
    render(ask(conn, "出差住宿标准多少？", who), show_trace=True)

    # ═══════════════════════════════════════════════════════════
    scene("2", "同一个问题，换一个发生日期",
          "这是整个系统最有说服力的一条：报销天然回溯，7月出差不能用10月的标准")

    q = "我7月12号出差的住宿标准多少？"
    print(f"\n{c('张明（销售部 / 华东）', 'dim')} 问：{c(q, 'bold')}")
    a2 = ask(conn, q, who)
    render(a2, show_trace=True)

    # 对照：忽略时间轴会答成什么样
    import sqlite3
    cur = conn.execute(
        """SELECT c.clause_no, c.text, d.version FROM policy_clause c
           JOIN policy_document d ON d.id=c.document_id
           WHERE d.title='差旅费管理办法' AND c.heading='住宿费标准'
             AND d.status='effective'"""
    ).fetchone()
    print(f"\n   {c('对照实验', 'bold')}：如果系统不支持双时间轴、只检索" +
          c("「最新一版」", "red") + "，会得到：")
    print(f"           {cur['clause_no']}（{cur['version']}，现行）")
    print(f"           │ {cur['text'][:60]}…")
    print(f"   {c('→ 500 元/天并不是张明 7 月 12 日出差时适用的标准。', 'yellow')}")
    print(f"   {c('   员工照着 500 去报销，会被周敏退回一轮，且没人知道问题出在哪。', 'yellow')}")

    # ═══════════════════════════════════════════════════════════
    scene("3", "问一个需要结合事实判断的问题",
          "验证：助手主动放弃裁量。它给原文，但不替周敏下结论")

    q3 = "我这个月请客户吃饭那笔能报多少？"
    print(f"\n{c('张明', 'dim')} 问：{c(q3, 'bold')}")
    render(ask(conn, q3, who), show_trace=True)

    # ═══════════════════════════════════════════════════════════
    scene("4", "单据状态查询 + 行级授权",
          "验证：只能看到自己的单；而且只转述字段，不预测结论")

    print(f"\n{c('张明', 'dim')} 问：{c('我上个月那笔为什么被退？', 'bold')}")
    render(ask(conn, "我上个月那笔为什么被退？", who))

    print(f"\n{c('张明', 'dim')} 问：{c('这笔应该快通过了吧？', 'bold')}")
    render(ask(conn, "这笔应该快通过了吧？", who))

    # ═══════════════════════════════════════════════════════════
    scene("5", "越权测试：用 A 的会话查 B 的单",
          "验证：拦截率必须 100%，且事迹留痕可供周敏事后查")

    q5 = "帮我查一下李静的单子到哪了"
    print(f"\n{c('张明', 'dim')} 问：{c(q5, 'bold')}")
    render(ask(conn, q5, who))

    print(f"\n   {c('再直接调用数据接口试一次：', 'dim')}")
    tickets = query_my_tickets(who, conn=conn)
    print(f"   query_my_tickets(张明) → 返回 {len(tickets)} 条："
          f"{[t['ticket_no'] for t in tickets]}")
    print(f"   {c('李静的单据 QH20260920-005 不在其中 —— 它在 SQL 层就被 filter 掉了。', 'green')}")

    print(f"\n   {c('为什么挡得住：', 'bold')}接口签名里压根没有 applicant_id 参数 ——")
    import inspect
    print(f"   signature = ask.query_my_tickets{inspect.signature(query_my_tickets)}")
    print(f"   {c('越权值不是被“校验”拦住的，是根本没有入口可以传。', 'green')}")

    q6 = "帮我把 QH20260915-017 的金额改成 500"
    print(f"\n{c('张明', 'dim')} 问：{c(q6, 'bold')}")
    render(ask(conn, q6, who))

    # ═══════════════════════════════════════════════════════════
    scene("6", "乔姐的待确认队列",
          "验证：助手提议、乔姐批准。助手永远不自动改制度元数据")

    q7 = "打车能报吗？"
    print(f"\n{c('张明', 'dim')} 问：{c(q7, 'bold')}")
    a7 = ask(conn, q7, who)
    render(a7)

    rv = rows(conn, "SELECT * FROM metadata_review_queue WHERE status='pending'")
    print(f"\n   {c('乔姐工作台 · 待确认 1 条', 'bold')}")
    for r in rv:
        import json
        prop = json.loads(r["proposal"])
        print(f"   ┌ {c('疑似新版本', 'yellow')}：《{r['doc_id']}》")
        print(f"   │ 助手抽取到的候选值：版本 {prop['version']}，"
              f"生效日 {prop['effective_from']}，替代 {prop['supersedes_id']}")
        print(f"   │ 置信度：{r['confidence']}（低于阈值，所以不会自动生效）")
        print(f"   │ 依据：{r['evidence']}")
        print(f"   └ 操作：[确认] [修改] [忽略]")

    print(f"\n   {c('助手提议、乔姐批准：', 'bold')}在乔姐点头之前，v2 被硬闸门挡在候选集外，")
    print(f"   上面的回答只能依据已确认的条款 —— 而不是静默采用未经确认的新元数据。")
    print(f"   （元数据抽取错误的后果是「静默给错答案」，这是所有失败模式里最坏的一种。）")

    print(f"\n   {c('现在乔姐点「确认」：', 'bold')}")
    from src.ingest.queue import confirm_review
    res = confirm_review(conn, "RV-市内交通费管理办法_v2", by="乔姐")
    print(f"   → {res['confirmed_by']} 确认了《{res['doc_id']}》{res['version']}，"
          f"生效日 {res['effective_from']}")

    print(f"\n   {c('再问同一个问题一次：', 'bold')}")
    print(f"\n{c('张明', 'dim')} 问：{c(q7, 'bold')}")
    a7b = ask(conn, q7, who)
    render(a7b)

    print(f"\n   {c('同一个问题的答案变了 —— 但变的是乔姐的决定，不是助手的判断。', 'green', 'bold')}")
    print(f"   助手全程没有改过一个字的制度，它只是把候选和依据摆出来，等乔姐点头。")

    left = rows(conn, "SELECT COUNT(*) n FROM metadata_review_queue WHERE status='pending'")[0]["n"]
    print(f"   待确认队列剩余：{left} 条")

    # ═══════════════════════════════════════════════════════════
    scene("7", "审计日志（周敏视角）",
          "周敏一定会问“怎么保证别人查不到别人的单”，答案要可验证")

    denied = audit_trail(conn, result_code="denied")
    print(f"\n   {c(f'越权拦截记录 {len(denied)} 条：', 'bold')}")
    for d in denied[:5]:
        print(f"   · {d['ts'][:19]}  {d['actor_name']}({d['actor_employee']})  "
              f"{d['intent']}  「{d['query_text'][:30]}」")
        print(f"     └ result={c(d['result_code'], 'red')}  target={d['target']}  {d['detail'][:40]}")

    allog = audit_trail(conn, limit=200)
    print(f"\n   本次会话共写入 {len(allog)} 条审计日志，字段覆盖：")
    print(f"   actor_employee / channel / channel_userid / session_id / intent /")
    print(f"   query_text / target / result_code / as_of")
    print(f"\n   {c('actor_employee 一律取自 Identity，不是取自请求体。', 'green')}")

    print(f"\n{LINE}")
    print(c(" Demo 结束。这套系统的判断原则只有一句：", "bold"))
    print(c(" 宁可说“我不确定，请找乔姐/周敏”，也绝不编一句“应该可以报”。", "yellow"))
    print(LINE)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
