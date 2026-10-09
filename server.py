"""
server.py —— 演示用 Web 服务（Python 标准库，零第三方依赖）。

启动：
    python server.py
然后浏览器打开 http://127.0.0.1:8787

每个请求独立建立 SQLite 连接，避免多线程共享游标的问题。
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.answer.assistant import ask
from src.auth.gateway import Identity, audit_trail, query_my_tickets, resolve_identity
from src.db import connect, rows
from src.ingest.queue import confirm_review, list_pending, reject_review
from src.seed import seed

WEB = ROOT / "web"
PORT = 8787


def answer_to_dict(a) -> dict:
    return {
        "mode": a.mode,
        "mode_label": a.mode_label,
        "headline": a.headline,
        "body": a.body,
        "citations": [
            {"label": c.label, "period": c.period, "source_uri": c.source_uri,
             "text": c.text, "role": c.role, "note": c.note}
            for c in a.citations
        ],
        "notices": a.notices,
        "actions": a.actions,
        "trace": {k: v for k, v in a.trace.items()},
    }


def policy_library(conn) -> list[dict]:
    rs = rows(
        conn,
        """SELECT id,title,version,effective_from,effective_to,status,doc_kind,
                  superseded_by,meta_confirmed_by,publisher,scope_dept,scope_region
           FROM policy_document ORDER BY title, effective_from""",
    )
    out = []
    for r in rs:
        d = dict(r)
        import json as _j
        d["scope_dept"] = _j.loads(d["scope_dept"]) if d["scope_dept"] else None
        d["scope_region"] = _j.loads(d["scope_region"]) if d["scope_region"] else None
        out.append(d)
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 安静一点
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _html(self, p: Path) -> None:
        if not p.exists():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, p.read_bytes(), "text/html; charset=utf-8")

    # ── GET ────────────────────────────────────────────────────
    def do_GET(self) -> None:
        u = urlparse(self.path)
        qs = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            self._html(WEB / "index.html")
            return

        conn = connect()
        try:
            if u.path == "/api/me":
                uid = qs.get("userid", ["wm_zhangming"])[0]
                try:
                    ident = resolve_identity(uid)
                    self._json({"ok": True, **ident.as_dict(), "userid": uid})
                except PermissionError as e:
                    self._json({"ok": False, "error": str(e)})

            elif u.path == "/api/tickets":
                uid = qs.get("userid", ["wm_zhangming"])[0]
                ident = resolve_identity(uid)
                ts = query_my_tickets(ident, conn=conn)
                self._json({"ok": True, "identity": ident.as_dict(), "tickets": ts})

            elif u.path == "/api/policies":
                self._json({"ok": True, "documents": policy_library(conn)})

            elif u.path == "/api/review":
                pend = list_pending(conn)
                for p in pend:
                    p.pop("source_uri", None)
                self._json({"ok": True, "pending": pend})

            elif u.path == "/api/audit":
                kind = qs.get("kind", [""])[0]
                if kind == "denied":
                    logs = audit_trail(conn, result_code="denied")
                else:
                    logs = audit_trail(conn, limit=100)
                self._json({"ok": True, "logs": logs})

            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        finally:
            conn.close()

    # ── POST ───────────────────────────────────────────────────
    def do_POST(self) -> None:
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

        conn = connect()
        try:
            if u.path == "/api/ask":
                uid = payload.get("userid", "wm_zhangming")
                q = (payload.get("q") or "").strip()
                if not q:
                    self._json({"ok": False, "error": "问题为空"}, 400)
                    return
                ident = resolve_identity(uid)
                a = ask(conn, q, ident)
                conn.commit()
                self._json({"ok": True, "answer": answer_to_dict(a),
                            "identity": ident.as_dict()})

            elif u.path == "/api/review/confirm":
                rid = payload.get("id")
                res = confirm_review(conn, rid, by=payload.get("by", "乔姐"))
                self._json({"ok": res.get("ok", False), **res})

            elif u.path == "/api/review/reject":
                rid = payload.get("id")
                res = reject_review(conn, rid, by=payload.get("by", "乔姐"))
                self._json({"ok": res.get("ok", False), **res})

            elif u.path == "/api/reset":
                # 把数据库恢复到 demo 初始状态（含乔姐未确认的那一条）
                conn.close()
                seed(reset=True)
                self._json({"ok": True, "note": "已重置"})
                return

            else:
                self._json({"ok": False, "error": "no route"}, 404)
        except Exception as e:  # 演示环境，把错误显式回传给前端
            import traceback
            traceback.print_exc()
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            try:
                conn.close()
            except Exception:
                pass


def main() -> None:
    seed(reset=True)  # 每次启动回到干净的 demo 初始状态
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"\n  青禾报销助手 · Web Demo")
    print(f"  → http://127.0.0.1:{PORT}\n")
    print("  Ctrl+C 退出\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
        srv.server_close()


if __name__ == "__main__":
    main()
