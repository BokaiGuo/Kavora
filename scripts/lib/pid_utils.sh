#!/usr/bin/env bash

# PID-file based process management helpers.

is_pid_running() {
  local pid="$1"
  kill -0 "${pid}" >/dev/null 2>&1
}

wait_for_pid_exit() {
  local pid="$1"
  local timeout_s="${2:-30}"
  local start_s now_s
  start_s="$(date +%s)"
  while is_pid_running "${pid}"; do
    now_s="$(date +%s)"
    if [[ $((now_s - start_s)) -ge "${timeout_s}" ]]; then
      return 1
    fi
    sleep 1
  done
  return 0
}

read_pid_file() {
  local pid_file="$1"
  if [[ -f "${pid_file}" ]]; then
    tr -d '[:space:]' <"${pid_file}"
  fi
}

stop_with_pid_file() {
  local pid_file="$1"
  local label="${2:-process}"
  local pid
  pid="$(read_pid_file "${pid_file}")"
  if [[ -z "${pid}" ]]; then
    return 0
  fi
  if is_pid_running "${pid}"; then
    echo "[pid] stopping ${label} pid=${pid}"
    kill "${pid}" >/dev/null 2>&1 || true
    if ! wait_for_pid_exit "${pid}" "${STOP_WAIT_MAX_S:-30}"; then
      echo "[pid] force killing ${label} pid=${pid} after timeout"
      kill -9 "${pid}" >/dev/null 2>&1 || true
      wait_for_pid_exit "${pid}" "${STOP_KILL_WAIT_MAX_S:-10}" || true
    fi
  fi
  rm -f "${pid_file}"
}

start_with_pid_file() {
  local pid_file="$1"
  shift
  "$@" &
  local pid="$!"
  echo "${pid}" >"${pid_file}"
  echo "[pid] started pid=${pid}, pid_file=${pid_file}"
}
