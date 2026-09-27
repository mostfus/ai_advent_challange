#!/bin/sh
# Keeps events_mcp_server.py running on a VPS where you have no root.
#
# Without root there is no systemd service to install, so the supervisor is
# made of two things every user has: cron and flock(1). Cron runs this every
# five minutes. The server holds data/server.lock for as long as it lives, so
# while it is up this does nothing; once it has died - a crash, a reboot - the
# lock is free and this starts it again.
#
#   ./keepalive.sh install     put itself in your crontab and start the server
#   ./keepalive.sh status      running or not, and the end of the log
#   ./keepalive.sh restart     stop and start again - after a `git pull`
#   ./keepalive.sh stop        stop it (cron brings it back within 5 minutes)
#   ./keepalive.sh uninstall   out of the crontab, and stopped
#   ./keepalive.sh             start it unless it is running - what cron calls
#
# The server listens on 127.0.0.1 only (EVENTS_HOST's default) and is reached
# through an SSH tunnel: no port to open, no certificate to read. Its settings
# are in .env next to this file.

cd "$(dirname "$0")" || exit 1
mkdir -p data
LOCK=data/server.lock
PID=data/server.pid
LOG=data/server.log
LOG_MAX_BYTES=5000000
CRON_LINE="*/5 * * * * $PWD/keepalive.sh"

running() { ! flock -n "$LOCK" true; }

stop() {
    running || return 0
    kill "$(cat "$PID" 2>/dev/null)" 2>/dev/null
    flock -w 30 "$LOCK" true || { echo "still running after 30 s" >&2; return 1; }
}

uncron() { crontab -l 2>/dev/null | grep -vF "$PWD/keepalive.sh"; }

case "${1:-start}" in
    start) ;;
    install)
        { uncron; echo "$CRON_LINE"; } | crontab - || exit 1
        echo "crontab: $CRON_LINE" ;;
    uninstall)
        uncron | crontab -
        stop
        exit ;;
    stop)
        stop
        exit ;;
    restart)
        stop || exit 1 ;;
    status)
        [ -f "$LOG" ] && tail -n 5 "$LOG"
        if running; then echo "running, pid $(cat "$PID" 2>/dev/null)"; exit 0; fi
        echo "not running"
        exit 1 ;;
    *)
        echo "usage: $0 [install|status|restart|stop|uninstall]" >&2
        exit 2 ;;
esac

# The log is only ever appended to, so it can be cut in place under the
# running server - no restart, and nothing left writing into a deleted file.
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt "$LOG_MAX_BYTES" ]; then
    cp "$LOG" "$LOG.1" && : > "$LOG"
fi

if running; then
    [ -t 1 ] && echo "already running, pid $(cat "$PID" 2>/dev/null)"
    exit 0
fi
if [ ! -x .venv/bin/python ]; then
    echo "no .venv here yet - run: uv sync --frozen --python 3.11" >&2
    exit 1
fi

# setsid: a session of its own, so logging out of ssh does not take it down.
# -u: print() goes to the log as it happens, not when a buffer fills.
setsid flock -n "$LOCK" sh -c 'echo $$ > data/server.pid; exec .venv/bin/python -u events_mcp_server.py' \
    >> "$LOG" 2>&1 < /dev/null &

# By hand, say how it went; from cron, stay quiet - cron mails any output.
if [ -t 1 ]; then
    sleep 3
    if running; then
        echo "started, pid $(cat "$PID" 2>/dev/null) - log: $LOG"
    else
        tail -n 20 "$LOG"
        echo "did not start - the log above says why"
        exit 1
    fi
fi
