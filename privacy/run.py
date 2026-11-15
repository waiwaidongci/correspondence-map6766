#!/usr/bin/env python3
"""启动隐私分组发布服务（零第三方依赖）。

用法：python3 -m privacy.run [port]
默认端口 18333，数据存于 privacy/data/privacy.db（可用 PRIVACY_DB_PATH 覆盖）。
"""
from __future__ import annotations

import sys

from privacy.server.app import serve

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18333
    serve(port=port)
