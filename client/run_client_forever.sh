#!/bin/sh
set -eu

APP_DIR="/opt/loonginx-client"
PYTHON_BIN="/opt/python3.9/bin/python3.9"
MAIN_FILE="$APP_DIR/main.py"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/supervisor.log"
SERIAL_PORT="/dev/ttyS6"
LOOP_DELAY=2
LOCK_DIR="$APP_DIR/.supervisor.lock"

mkdir -p "$LOG_DIR"

log() {
  printf '%s [supervisor] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG_FILE"
}

kill_client_processes() {
  pkill -f "$MAIN_FILE" 2>/dev/null || true
}

release_serial_port() {
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "$SERIAL_PORT" 2>/dev/null || true
  fi
}

cleanup_before_start() {
  kill_client_processes
  sleep 1
  release_serial_port
  sleep 1
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" >"$LOCK_DIR/pid"
    return 0
  fi

  if [ -f "$LOCK_DIR/pid" ]; then
    OTHER_PID="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$OTHER_PID" ] && kill -0 "$OTHER_PID" 2>/dev/null; then
      log "another supervisor is already running pid=$OTHER_PID, exiting"
      exit 0
    fi
  fi

  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
  printf '%s\n' "$$" >"$LOCK_DIR/pid"
}

release_lock() {
  if [ -f "$LOCK_DIR/pid" ] && [ "$(cat "$LOCK_DIR/pid" 2>/dev/null || true)" = "$$" ]; then
    rm -rf "$LOCK_DIR"
  fi
}

trap 'log "stop requested"; kill_client_processes; release_serial_port; release_lock; exit 0' INT TERM EXIT

acquire_lock
cd "$APP_DIR"

while true; do
  cleanup_before_start
  log "starting client"
  "$PYTHON_BIN" -u "$MAIN_FILE" >>"$LOG_FILE" 2>&1 || true
  log "client exited, releasing $SERIAL_PORT and retrying"
  kill_client_processes
  release_serial_port
  sleep "$LOOP_DELAY"
done
