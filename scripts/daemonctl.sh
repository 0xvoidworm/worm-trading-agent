#!/usr/bin/env bash
# Daemon controller — start/stop/status/log untuk auto-refresh daemon
#
# Usage:
#   ./scripts/daemonctl.sh start          # start (default cycle 300s)
#   ./scripts/daemonctl.sh start 900      # custom cycle 15 min
#   ./scripts/daemonctl.sh stop
#   ./scripts/daemonctl.sh status
#   ./scripts/daemonctl.sh log            # tail -f log
#   ./scripts/daemonctl.sh restart

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="$ROOT/data/daemon.pid"
LOGFILE="$ROOT/data/daemon.log"
PYBIN="$ROOT/.venv/bin/python"

cmd="${1:-status}"
cycle="${2:-300}"

is_running() {
  [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "$cmd" in
  start)
    if is_running; then
      echo "[daemonctl] already running (pid $(cat "$PIDFILE"))"
      exit 0
    fi
    mkdir -p "$ROOT/data"
    cd "$ROOT"
    nohup "$PYBIN" scripts/daemon.py --cycle "$cycle" >> "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    if is_running; then
      echo "[daemonctl] started (pid $(cat "$PIDFILE"), cycle ${cycle}s)"
      echo "[daemonctl] log: $LOGFILE"
    else
      echo "[daemonctl] FAILED to start — check $LOGFILE"
      rm -f "$PIDFILE"
      exit 1
    fi
    ;;

  stop)
    if ! is_running; then
      echo "[daemonctl] not running"
      rm -f "$PIDFILE"
      exit 0
    fi
    pid="$(cat "$PIDFILE")"
    echo "[daemonctl] sending SIGTERM to $pid"
    kill -TERM "$pid"
    # wait up to 30s for graceful shutdown
    for i in $(seq 1 30); do
      if ! kill -0 "$pid" 2>/dev/null; then
        echo "[daemonctl] stopped"
        rm -f "$PIDFILE"
        exit 0
      fi
      sleep 1
    done
    echo "[daemonctl] WARNING: did not stop in 30s, sending SIGKILL"
    kill -KILL "$pid" || true
    rm -f "$PIDFILE"
    ;;

  status)
    if is_running; then
      pid="$(cat "$PIDFILE")"
      echo "[daemonctl] RUNNING (pid $pid)"
      if [[ -f "$ROOT/data/daemon.heartbeat" ]]; then
        echo "--- heartbeat ---"
        cat "$ROOT/data/daemon.heartbeat"
      fi
    else
      echo "[daemonctl] NOT RUNNING"
    fi
    ;;

  log)
    tail -f "$LOGFILE"
    ;;

  restart)
    "$0" stop
    "$0" start "$cycle"
    ;;

  *)
    echo "Usage: $0 {start [cycle_sec]|stop|status|log|restart}"
    exit 1
    ;;
esac
