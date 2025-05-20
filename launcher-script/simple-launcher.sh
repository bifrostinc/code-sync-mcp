#!/bin/sh
# simple-launcher.sh - Script to manage application restarts with proper process tree handling

: "${WATCH_DIR:=/app-files}"
: "${APP_ROOT:=/app}"
: "${TEST_MODE:=false}"

SIDECAR_DIR="${WATCH_DIR}/.sidecar"
LAUNCHER_DIR="${WATCH_DIR}/.launcher"

APP_PID_FILE="${SIDECAR_DIR}/simple-app.pid"
APP_PGID_FILE="${SIDECAR_DIR}/simple-app-pgid.pid"  # Added to track process group ID

# Function to kill entire process group
kill_process_tree() {
    local pid=$1
    local signal=${2:-15}  # Default to SIGTERM
    
    if [ -f "$APP_PGID_FILE" ]; then
        pgid=$(cat "$APP_PGID_FILE")
        echo "Sending signal $signal to process group $pgid"
        kill -$signal -$pgid 2>/dev/null
        
        # Wait up to 5 seconds for graceful termination
        wait_time=0
        while kill -0 -$pgid 2>/dev/null && [ "$wait_time" -lt 5 ]; do
            sleep 1
            wait_time=$((wait_time + 1))
        done
        
        # Force kill if still running
        if kill -0 -$pgid 2>/dev/null; then
            echo "Process group did not stop gracefully. Sending SIGKILL."
            kill -9 -$pgid 2>/dev/null
        fi
    elif [ -n "$pid" ]; then
        echo "No process group ID found, falling back to PID $pid"
        kill -$signal $pid 2>/dev/null
        
        # Wait up to 5 seconds
        wait_time=0
        while kill -0 $pid 2>/dev/null && [ "$wait_time" -lt 5 ]; do
            sleep 1
            wait_time=$((wait_time + 1))
        done
        
        # Force kill if still running
        if kill -0 $pid 2>/dev/null; then
            kill -9 $pid 2>/dev/null
        fi
    fi
}

# Function to start or restart the application
start_app() {
    # Kill previous instance if it exists
    if [ -f "$APP_PID_FILE" ]; then
        old_pid=$(cat "$APP_PID_FILE")
        kill_process_tree "$old_pid"
    fi
    
    # Start the application in a new process group
    echo "Starting application with command: sh -c \"$*\""
    (
        # Use setsid to create a new process group
        setsid sh -c "$*" &
        
        APP_PID=$!
        APP_PGID=$APP_PID  # In a new session, PGID equals PID
        
        # Save PIDs to files
        echo "$APP_PID" > "$APP_PID_FILE"
        echo "$APP_PGID" > "$APP_PGID_FILE"
        echo "Application started with PID $APP_PID, PGID $APP_PGID"
        
        # Wait for the process and capture exit status
        wait $APP_PID
        _exit_status=$?
        echo "Application command ('$*') exited with status $_exit_status" > "${LAUNCHER_DIR}/app.status"
    ) &
    
    # Give processes time to initialize
    sleep 1
}

# Function to handle SIGHUP
handle_sighup() {
    echo "Received SIGHUP, restarting application"
    start_app "$@"
}

# Set up signal handlers
trap 'handle_sighup "$@"' HUP
trap 'echo "Received SIGTERM, shutting down"; kill_process_tree "$(cat "$APP_PID_FILE" 2>/dev/null)"; exit 0' TERM INT

# Initial start
start_app "$@"

# Keep running to handle signals
while true; do
    sleep 1
    # Check if app is still running
    if [ -f "$APP_PID_FILE" ] && [ -f "$APP_PGID_FILE" ]; then
        current_pid=$(cat "$APP_PID_FILE")
        current_pgid=$(cat "$APP_PGID_FILE")
        
        # Try to check the process group rather than just the PID
        if ! kill -0 -$current_pgid 2>/dev/null; then
            echo "Application process group exited. Restarting."
            start_app "$@"
        fi
    else
        echo "PID/PGID file missing, restarting application"
        start_app "$@"
    fi
done