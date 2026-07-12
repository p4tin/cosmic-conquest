#!/bin/bash

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$APP_DIR/.app.pid"
PYTHON_BIN="$APP_DIR/venv/bin/python" # Assuming venv exists based on previous lsof output

start() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "App is already running (PID: $(cat $PID_FILE))"
        return
    fi

    echo "Starting Cosmic Conquest..."
    export PYTHONPATH="$APP_DIR"
    
    # Run in background and save PID
    nohup $PYTHON_BIN src/main.py > "$APP_DIR/app.log" 2>&1 &
    echo $! > "$PID_FILE"
    
    echo "App started with PID $!. Logs are at $APP_DIR/app.log"
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "PID file not found. Is the app running?"
        return
    fi

    PID=$(cat "$PID_FILE")
    if kill -0 $PID 2>/dev/null; then
        echo "Stopping app (PID: $PID)..."
        kill $PID
        rm "$PID_FILE"
        echo "App stopped."
    else
        echo "Process $PID not found. Cleaning up PID file."
        rm "$PID_FILE"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "App is running (PID: $(cat $PID_FILE))"
    else
        echo "App is stopped."
    fi
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) stop; start ;;
    status) status ;;
    *) echo "Usage: $0 {start|stop|restart|status}" ;;
esac
