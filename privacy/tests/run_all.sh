#!/usr/bin/env bash
# 一键运行全部测试（零第三方依赖，仅需 Python 3.11+）
set -euo pipefail
cd "$(dirname "$0")/../.."
echo "== 1/4 引擎正确性（含暴力枚举对照） =="
python3 -m privacy.tests.test_engine
echo "== 2/4 服务集成（工作流/累计泄露/校订/并发） =="
python3 -m privacy.tests.test_service
echo "== 3/4 安全失败与 12x12 压力 =="
python3 -m privacy.tests.test_safety
echo "== 4/4 HTTP 端到端 =="
python3 -m privacy.tests.test_http
echo "全部测试通过"
