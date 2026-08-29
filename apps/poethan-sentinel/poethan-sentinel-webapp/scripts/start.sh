#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
DATA_ROOT="${POETHAN_SENTINEL_DATA_DIR:-${HOME}/Library/Application Support/Poethan Sentinel Web}"
PID_FILE="${DATA_ROOT}/controller.pid"
LOG_FILE="${DATA_ROOT}/controller.log"
PORT="${POETHAN_SENTINEL_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

print_runtime_info() {
  local state="$1"
  local process_id="$2"
  printf '%s\n' \
    "Poethan Sentinel ${state}" \
    "  地址：${URL}" \
    "  PID：${process_id}" \
    "  日志：${LOG_FILE}" \
    "  停止：${ROOT}/scripts/stop.sh"
}

is_controller_process() {
  local process_id="$1"
  local command_line
  local process_cwd
  [[ "${process_id}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${process_id}" 2>/dev/null || return 1
  command_line="$(ps -p "${process_id}" -o command= 2>/dev/null || true)"
  [[ "${command_line}" == *uvicorn* && "${command_line}" == *app.main:app* && "${command_line}" == *"--port ${PORT}"* ]] || return 1
  process_cwd="$(lsof -a -p "${process_id}" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1 || true)"
  [[ "${process_cwd}" == "${ROOT}" || "${command_line}" == *"${ROOT}/controller"* ]]
}

listener_pids() {
  lsof -nP -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | sort -u || true
}

find_controller_pid() {
  local process_id
  while IFS= read -r process_id; do
    if is_controller_process "${process_id}"; then
      printf '%s\n' "${process_id}"
      return 0
    fi
  done < <(listener_pids)
  return 1
}

read_pid_file() {
  local saved_pid
  [[ -f "${PID_FILE}" ]] || return 1
  IFS= read -r saved_pid < "${PID_FILE}" || true
  [[ "${saved_pid}" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "${saved_pid}"
}

is_ready() {
  curl -fsS --max-time 1 "${URL}/api/v1/health" >/dev/null 2>&1
}

mkdir -p "${DATA_ROOT}"
if [[ ! -x "${ROOT}/controller/.venv/bin/uvicorn" || ! -f "${ROOT}/frontend/dist/index.html" ]]; then
  echo "尚未安装，请先执行 ${ROOT}/scripts/install.sh" >&2
  exit 1
fi

EXISTING_PID="$(read_pid_file || true)"
if [[ -n "${EXISTING_PID}" ]] && is_controller_process "${EXISTING_PID}"; then
  if is_ready; then
    print_runtime_info "已在后台运行" "${EXISTING_PID}"
    exit 0
  fi
  echo "Poethan Sentinel 进程存在但健康检查未通过（PID ${EXISTING_PID}，${URL}）" >&2
  exit 1
fi
if [[ -f "${PID_FILE}" ]]; then
  rm -f "${PID_FILE}"
fi

EXISTING_PID="$(find_controller_pid || true)"
if [[ -n "${EXISTING_PID}" ]]; then
  printf '%s\n' "${EXISTING_PID}" > "${PID_FILE}"
  if ! is_ready; then
    echo "Poethan Sentinel 正在监听但健康检查未通过（PID ${EXISTING_PID}，${URL}）" >&2
    exit 1
  fi
  print_runtime_info "已在后台运行（已修复 PID 文件）" "${EXISTING_PID}"
  exit 0
fi

OCCUPIED_PIDS="$(listener_pids | paste -sd ',' -)"
if [[ -n "${OCCUPIED_PIDS}" ]]; then
  echo "启动失败：端口 ${PORT} 已被其他进程占用（PID ${OCCUPIED_PIDS}）" >&2
  exit 1
fi

cd "${ROOT}"
nohup "${ROOT}/controller/.venv/bin/uvicorn" app.main:app \
  --app-dir "${ROOT}/controller" --host 127.0.0.1 --port "${PORT}" \
  >> "${LOG_FILE}" 2>&1 &
PID="$!"
printf '%s\n' "${PID}" > "${PID_FILE}"

for ((attempt = 0; attempt < 50; attempt++)); do
  if is_controller_process "${PID}" && is_ready; then
    print_runtime_info "已启动并在后台运行" "${PID}"
    exit 0
  fi
  if ! kill -0 "${PID}" 2>/dev/null; then
    break
  fi
  sleep 0.2
done

if is_controller_process "${PID}"; then
  kill "${PID}" 2>/dev/null || true
fi
rm -f "${PID_FILE}"
echo "启动失败：Controller 未在 10 秒内就绪，请查看 ${LOG_FILE}" >&2
tail -n 20 "${LOG_FILE}" >&2 || true
exit 1
