#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_ROOT="${POETHAN_SENTINEL_DATA_DIR:-${HOME}/Library/Application Support/Poethan Sentinel Web}"
PID_FILE="${DATA_ROOT}/controller.pid"
LOG_FILE="${DATA_ROOT}/controller.log"
URL="http://127.0.0.1:8765"

open_browser() {
  if [[ "${POETHAN_SENTINEL_NO_OPEN:-0}" != "1" ]] && command -v open >/dev/null 2>&1; then
    open "${URL}" >/dev/null 2>&1 || true
  fi
}

is_controller_process() {
  local process_id="$1"
  local command_line
  command_line="$(ps -p "${process_id}" -o command= 2>/dev/null || true)"
  [[ "${command_line}" == *uvicorn* && "${command_line}" == *app.main:app* ]]
}

mkdir -p "${DATA_ROOT}"
if [[ ! -x "${ROOT}/controller/.venv/bin/uvicorn" || ! -f "${ROOT}/frontend/dist/index.html" ]]; then
  echo "尚未安装，请先执行 ${ROOT}/scripts/install.sh" >&2
  exit 1
fi

if [[ -f "${PID_FILE}" ]]; then
  EXISTING_PID="$(tr -cd '0-9' < "${PID_FILE}")"
  if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" 2>/dev/null && is_controller_process "${EXISTING_PID}"; then
    echo "Poethan Sentinel 已运行（PID ${EXISTING_PID}）"
    open_browser
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

cd "${ROOT}"
nohup "${ROOT}/controller/.venv/bin/uvicorn" app.main:app \
  --app-dir "${ROOT}/controller" --host 127.0.0.1 --port 8765 \
  >> "${LOG_FILE}" 2>&1 &
PID="$!"
printf '%s\n' "${PID}" > "${PID_FILE}"
sleep 1

if ! kill -0 "${PID}" 2>/dev/null; then
  rm -f "${PID_FILE}"
  echo "启动失败，请查看 ${LOG_FILE}" >&2
  exit 1
fi

echo "Poethan Sentinel 已启动：${URL}（PID ${PID}）"
open_browser
