"""标准库 HTTP 服务：隐私发布 API + 静态前端。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import db, service
from .engine import Inconclusive

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web"


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        raise service.ValidationError("请求体不是合法 JSON")


class Handler(BaseHTTPRequestHandler):
    server_version = "PrivacyRelease/1.0"

    def log_message(self, fmt, *args):  # 安静日志
        pass

    def _send(self, code: int, payload, ctype="application/json; charset=utf-8"):
        if ctype.startswith("application/json"):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            body = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, rel: str):
        target = (STATIC / rel).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            self._send(404, {"error": "not found"})
            return
        ctype = "text/html; charset=utf-8"
        if rel.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif rel.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/health":
                return self._send(200, {"status": "ok"})
            if path == "/api/studies":
                return self._send(200, service.list_studies())
            if path.startswith("/api/studies/") and path.endswith("/publications"):
                sid = int(path.split("/")[3])
                return self._send(200, service.list_publications(sid))
            if path.startswith("/api/studies/"):
                sid = int(path.split("/")[3])
                s = service.get_study(sid)
                if not s:
                    return self._send(404, {"error": "研究不存在"})
                return self._send(200, s)
            if path.startswith("/api/download/"):
                pid = int(path.split("/")[3])
                return self._download(pid)
            if path in ("/", "/index.html"):
                return self._file("index.html")
            if path.startswith("/web/"):
                return self._file(path[5:])
            return self._send(404, {"error": "not found"})
        except service.ValidationError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": f"服务器错误: {e}"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = _read_json(self)
            if path == "/api/studies":
                s = service.create_study(
                    data.get("name"), data.get("description", ""),
                    data.get("row_labels"), data.get("col_labels"),
                    data.get("base_table"), int(data.get("threshold", 5)),
                )
                return self._send(201, s)
            m = path.strip("/").split("/")
            # /api/studies/{id}/preview | /publish | /revise
            if len(m) == 4 and m[:2] == ["api", "studies"]:
                sid = int(m[2]); action = m[3]
                if action == "preview":
                    return self._send(200, service.publish(
                        sid, data.get("title", "预览"), data.get("row_groups"),
                        data.get("col_groups"), data.get("extra_suppressed"), True))
                if action == "publish":
                    return self._send(201, service.publish(
                        sid, data.get("title", "发布"), data.get("row_groups"),
                        data.get("col_groups"), data.get("extra_suppressed"), False))
                if action == "revise":
                    return self._send(200, service.revise_study(
                        sid, data.get("base_table"), data.get("row_labels"),
                        data.get("col_labels"), int(data.get("threshold", 5))))
            return self._send(404, {"error": "not found"})
        except service.ValidationError as e:
            return self._send(400, {"error": str(e)})
        except Inconclusive as e:
            return self._send(409, {"error": f"推断不确定，已安全拒绝: {e}"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": f"服务器错误: {e}"})

    def _download(self, pid: int):
        with db.read_transaction() as conn:
            row = conn.execute("SELECT * FROM publications WHERE id=?", (pid,)).fetchone()
            if not row:
                return self._send(404, {"error": "发布不存在"})
            pub = db.pub_to_dict(row)
            study = service.get_study(pub["study_id"])
        text = service.csv_text(pub, study)
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header(
            "Content-Disposition",
            f"attachment; filename=publication_{pid}.csv")
        body = ("﻿" + text).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "0.0.0.0", port: int = 18333):
    db.init_db()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"隐私发布服务已启动: http://localhost:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
