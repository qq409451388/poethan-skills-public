#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
DATA_ROOT="${POETHAN_SENTINEL_DATA_DIR:-${HOME}/Library/Application Support/Poethan Sentinel Web}"
PID_FILE="${DATA_ROOT}/controller.pid"
PORT="${POETHAN_SENTINEL_PORT:-8765}"

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

PID="$(read_pid_file || true)"
if [[ -n "${PID}" ]] && ! is_controller_process "${PID}"; then
  echo "忽略失效的 PID 文件（PID ${PID} 不属于当前 Poethan Sentinel）" >&2
  PID=""
fi
if [[ -z "${PID}" ]]; then
  PID="$(find_controller_pid || true)"
fi
if [[ -z "${PID}" ]]; then
  rm -f "${PID_FILE}"
  echo "Poethan Sentinel 未运行（端口 ${PORT} 未发现当前项目的 Controller）"
  exit 0
fi

printf '%s\n' "${PID}" > "${PID_FILE}"
kill "${PID}" 2>/dev/null || true
for ((attempt = 0; attempt < 50; attempt++)); do
  if ! kill -0 "${PID}" 2>/dev/null; then
    break
  fi
  sleep 0.2
done
if kill -0 "${PID}" 2>/dev/null; then
  echo "Controller 在 10 秒内未退出，正在强制停止 PID ${PID}" >&2
  kill -KILL "${PID}" 2>/dev/null || true
  for ((attempt = 0; attempt < 20; attempt++)); do
    kill -0 "${PID}" 2>/dev/null || break
    sleep 0.1
  done
fi
if kill -0 "${PID}" 2>/dev/null; then
  echo "停止失败：PID ${PID} 仍在运行" >&2
  exit 1
fi

rm -f "${PID_FILE}"
REMAINING_PID="$(find_controller_pid || true)"
if [[ -n "${REMAINING_PID}" ]]; then
  echo "停止不完整：端口 ${PORT} 仍有当前项目 Controller（PID ${REMAINING_PID}）" >&2
  exit 1
fi
OCCUPIED_PIDS="$(listener_pids | paste -sd ',' -)"
if [[ -n "${OCCUPIED_PIDS}" ]]; then
  echo "Poethan Sentinel 已停止，但端口 ${PORT} 随后被其他进程占用（PID ${OCCUPIED_PIDS}）" >&2
  exit 1
fi
echo "Poethan Sentinel 已停止（PID ${PID}，端口 ${PORT} 已释放）"
