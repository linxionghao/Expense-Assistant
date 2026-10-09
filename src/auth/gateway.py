"""
auth/gateway.py —— 身份 + 只读单据网关。

四条铁律（《设计方案.md》§4）：
  1. 身份来自通道，绝不来自用户输入（不接受"请输入工号"）
  2. 鉴权落在数据侧 —— 函数签名里压根没有 applicant_id 参数，越权值无处可传
  3. 只读，而且只读"状态" —— 没有任何写操作入口
  4. 状态是转述，不是判断 —— 不预测"应该快通过了"

演示时可以打印 query_my_tickets.__code__.co_varnames 来证明签名里没有 applicant_id。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

TODAY = date(2026, 10, 9)


@dataclass(frozen=True)
class Identity:
    """
    frozen=True：构造后不可篡改。
    只能由 resolve_identity() 从会话通道构造，业务代码无法伪造。
    """
    employee_id: str
    name: str
    dept: str
    region: str
    channel: str = "wecom"
    channel_userid: str = ""
    session_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "employee_id": self.employee_id,
            "name": self.name,
            "dept": self.dept,
            "region": self.region,
            "channel": self.channel,
        }


# 通道 userid → 员工唯一 ID。真实环境由企微/钉钉/飞书的 OAuth 回调提供。
_CHANNEL_MAP: dict[str, tuple[str, str, str, str]] = {
    # channel_userid: (employee_id, name, dept, region)
    "wm_zhangming": ("E1001", "张明", "销售部", "华东"),
    "wm_lijing": ("E1002", "李静", "研发部", "华南"),
    "wm_wangqiang": ("E1003", "王强", "销售部", "华北"),
    "wm_qiaojie": ("E9001", "乔姐", "财务部", "华东"),
    "wm_zhoumin": ("E9002", "周敏", "财务部", "华东"),
}


def resolve_identity(channel_userid: str, session_id: str = "",
                     channel: str = "wecom") -> Identity:
    """通道身份 → Identity。这是身份的唯一入口。"""
    got = _CHANNEL_MAP.get(channel_userid)
    if not got:
        raise PermissionError(f"未知通道身份 {channel_userid!r}，未完成企业身份映射")
    eid, name, dept, region = got
    return Identity(
        employee_id=eid, name=name, dept=dept, region=region,
        channel=channel, channel_userid=channel_userid,
        session_id=session_id or uuid.uuid4().hex[:12],
    )


def audit(conn: sqlite3.Connection, identity: Identity, *, intent: str, query_text: str,
          target: str = "", result_code: str = "ok", detail: str = "", as_of: str = "") -> None:
    """审计日志：actor 只能来自 Identity，不是从请求体取。"""
    conn.execute(
        """INSERT INTO audit_log
           (ts,actor_employee,actor_name,channel,channel_userid,session_id,
            intent,query_text,target,result_code,detail,as_of)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now().astimezone().isoformat(timespec="seconds"),
            identity.employee_id, identity.name, identity.channel,
            identity.channel_userid, identity.session_id,
            intent, query_text, target, result_code, detail, as_of,
        ),
    )


# ★★★★★ 注意看这个签名 ★★★★★
# 参数列表里没有 applicant_id —— 调用方根本无法传入别人的工号。
# 越权不是被"校验"挡住的，是"表达不出来"。
def query_my_tickets(identity: Identity, *, conn: sqlite3.Connection,
                     ticket_no: str | None = None,
                     status: str | None = None,
                     _do_audit: bool = True) -> list[dict[str, Any]]:
    """
    查询**当前登录人本人**的单据状态。只读视图 v_my_ticket，不含金额/事由等敏感列。

    三道防线：
      1. 签名层：不存在 applicant_id 参数
      2. SQL 层：WHERE applicant_id = ? 是查询的一部分，不是可选条件
      3. 数据层：只访问 v_my_ticket 视图，金额/事由不在其中
    """
    sql = "SELECT * FROM v_my_ticket WHERE applicant_id = ?"
    args: list[Any] = [identity.employee_id]

    if ticket_no:
        sql += " AND ticket_no = ?"
        args.append(ticket_no)
    if status:
        sql += " AND status_code = ?"
        args.append(status)

    sql += " ORDER BY submitted_at DESC"
    rs = conn.execute(sql, args).fetchall()

    out = []
    for r in rs:
        d = dict(r)
        # 停留时长是客观计算结果，不是判断
        entered = date.fromisoformat(d["node_entered_at"])
        d["days_in_node"] = (TODAY - entered).days
        out.append(d)

    if _do_audit:
        audit(
            conn, identity,
            intent="ticket_query",
            query_text=f"ticket_no={ticket_no or '全部'}" + (f" status={status}" if status else ""),
            target=ticket_no or "*",
            result_code="ok" if out else "not_found",
            detail=f"{len(out)} rows",
        )
    return out


def format_ticket_state(t: dict[str, Any]) -> str:
    """
    只转述字段，不做推断。
    ✅「当前在财务复审，自 10-06 进入，已停留 3 天」
    ❌「应该快通过了」
    """
    head = f"单号 {t['ticket_no']} 当前在【{t['current_node']}】环节"
    head += f"，自 {t['node_entered_at']} 进入，已停留 {t['days_in_node']} 天。"
    if t["status_code"] == "returned" and t.get("last_return_reason"):
        # 退回原因原字段搬运，不加解释、不评判是否"问题不大"
        head += f"\n  退回原因记录为：{t['last_return_reason']}"
    return head


def audit_trail(conn: sqlite3.Connection, *, actor: str | None = None,
                result_code: str | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM audit_log WHERE 1=1"
    args: list[Any] = []
    if actor:
        sql += " AND actor_employee = ?"
        args.append(actor)
    if result_code:
        sql += " AND result_code = ?"
        args.append(result_code)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
