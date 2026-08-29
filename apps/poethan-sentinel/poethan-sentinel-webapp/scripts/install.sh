#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
echo "[1/4] 创建或更新 Python 环境"
if [[ ! -x "$ROOT/controller/.venv/bin/python" ]]; then "$PYTHON_BIN" -m venv "$ROOT/controller/.venv"; fi
"$ROOT/controller/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/controller/.venv/bin/python" -m pip install -r "$ROOT/controller/requirements.txt"
echo "[2/4] 安装 Web 前端依赖"
cd "$ROOT/frontend"; npm ci --cache "$ROOT/.npm-cache"
echo "[3/4] 编译 Web 前端"
npm run build
echo "[4/4] 运行自检"
cd "$ROOT/controller"
POETHAN_SENTINEL_DATA_DIR="${TMPDIR:-/tmp}/poethan-sentinel-install-check" POETHAN_SENTINEL_TESTING=1 .venv/bin/pytest -q
echo; echo "安装或更新完成。执行：$ROOT/scripts/start.sh"
