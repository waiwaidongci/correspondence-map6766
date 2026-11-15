"""HTTP 端到端测试：真实起服务，走 JSON API 全流程。

运行：python3 -m privacy.tests.test_http
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from http.client import RemoteDisconnected
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="privacy_http_")
os.environ["PRIVACY_DB_PATH"] = str(Path(_tmp) / "http.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from privacy.server.app import serve  # noqa: E402

PORT = 18399
BASE = f"http://127.0.0.1:{PORT}"


def _server():
    serve(host="127.0.0.1", port=PORT)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def main():
    t = threading.Thread(target=_server, daemon=True)
    t.start()
    for _ in range(50):
        try:
            call("GET", "/health"); break
        except (RemoteDisconnected, ConnectionError, OSError):
            time.sleep(0.1)

    # 健康检查与静态页
    st, _ = call("GET", "/health"); assert st == 200
    with urllib.request.urlopen(BASE + "/", timeout=10) as r:
        assert r.status == 200 and "隐私分组发布" in r.read().decode("utf-8")
    with urllib.request.urlopen(BASE + "/web/app.js", timeout=10) as r:
        assert r.status == 200

    # 建研究
    body = {
        "name": "HTTP演示", "description": "e2e",
        "row_labels": ["R0", "R1", "R2"], "col_labels": ["C0", "C1", "C2"],
        "base_table": [[3, 10, 7], [9, 20, 11], [5, 8, 14]], "threshold": 5,
    }
    st, study = call("POST", "/api/studies", body); assert st == 201, study
    sid = study["id"]

    # 非法矩阵 400
    bad = dict(body, name="坏", base_table=[[-1, 0, 0], [0, 0, 0], [0, 0, 0]])
    st, err = call("POST", "/api/studies", bad); assert st == 400, err

    # 预览不落历史
    payload = {"title": "预览", "row_groups": [[0, 1, 2]],
               "col_groups": [[0, 1], [2]], "extra_suppressed": []}
    st, prev = call("POST", f"/api/studies/{sid}/preview", payload)
    assert st == 200 and prev["safe"] and prev["dry_run"]
    st, pubs = call("GET", f"/api/studies/{sid}/publications"); assert pubs == []

    # 正式发布（粗分组，安全）
    st, p1 = call("POST", f"/api/studies/{sid}/publish",
                  dict(payload, title="第一次"))
    assert st == 201 and p1["safe"], p1
    assert all(r["possible_values"] >= 2 for r in p1["ranges"])

    # 细分组发布 -> 联立后唯一 -> 拒绝
    fine = {"title": "过细", "row_groups": [[0], [1], [2]],
            "col_groups": [[0], [1], [2]], "extra_suppressed": []}
    st, p2 = call("POST", f"/api/studies/{sid}/publish", fine)
    assert st == 201 and p2["safe"] is False
    assert any("仅 1 个可行整数" in r for r in p2["reject_reasons"])

    # 历史：2 条，安全 1 条
    st, pubs = call("GET", f"/api/studies/{sid}/publications")
    assert len(pubs) == 2 and sum(p["safe"] for p in pubs) == 1

    # 下载安全发布 CSV
    safe_id = next(p["id"] for p in pubs if p["safe"])
    with urllib.request.urlopen(f"{BASE}/api/download/{safe_id}", timeout=10) as r:
        csv_text = r.read().decode("utf-8-sig")
        assert r.status == 200 and "行组合计" in csv_text and "隐去" in csv_text

    # 校订：有历史 -> 停用并克隆
    revised = {"row_labels": body["row_labels"], "col_labels": body["col_labels"],
               "base_table": [[4, 10, 7], [9, 20, 11], [5, 8, 14]], "threshold": 5}
    st, out = call("POST", f"/api/studies/{sid}/revise", revised)
    assert st == 200 and out["cloned"] is True
    st, old = call("GET", f"/api/studies/{sid}"); assert old["status"] == "retired"
    # 停用研究发布 -> 400
    st, err = call("POST", f"/api/studies/{sid}/publish", payload)
    assert st == 400, err

    print("test_http PASS：健康检查/静态页/CRUD/预览/发布/拒绝/CSV/校订停用 全部通过")


if __name__ == "__main__":
    main()
