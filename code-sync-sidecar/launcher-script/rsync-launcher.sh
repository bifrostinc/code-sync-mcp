#!/bin/sh
# rsync-launcher.sh - Script to manage application launches with hot-swap support using rsync

: "${WATCH_DIR:=/app-files}"
: "${APP_ROOT:=/app}"
: "${TEST_MODE:=false}"  # Add this to support testing mode

SIDECAR_DIR="${WATCH_DIR}/.sidecar"
LAUNCHER_DIR="${WATCH_DIR}/.launcher"
RSYNC_BINARY="/app/bin/rsync"

APP_PID_FILE="${SIDECAR_DIR}/app.pid"
APP_PGID_FILE="${SIDECAR_DIR}/app-pgid.pid"  # Added to track process group ID

# Wait for both directories to be created
while [ ! -d "${SIDECAR_DIR}" ] || [ ! -d "${LAUNCHER_DIR}" ]; do
    sleep 2
    echo "[code-sync] Waiting for directories to be created"
done

# Write the initial PID file with our own PID
mkdir -p "${LAUNCHER_DIR}"
echo $$ > "${LAUNCHER_DIR}/launcher.pid"

echo "[code-sync] Running rsync launcher script, watch_dir: ${WATCH_DIR} and app_root: ${APP_ROOT}"
COMMAND="sh -c \"$*\""
echo "[code-sync] Wrapping command: ${COMMAND}"

# Function to sync files from WATCH_DIR to APP_ROOT using rsync
update_files() {
    # Find the appropriate rsync binary
    rsync_binary=""
    for binary in "${SIDECAR_DIR}/rsync_"*; do
        if [ -x "$binary" ]; then
            rsync_binary="$binary"
            break
        fi
    done

    if [ -z "$rsync_binary" ]; then
        echo "[code-sync] Warning: No executable rsync binary found in ${SIDECAR_DIR}"
        return 1
    fi

    echo "[code-sync] Syncing files from ${WATCH_DIR} to ${APP_ROOT} using ${rsync_binary}"

    # Ensure APP_ROOT exists
    mkdir -p "${APP_ROOT}"

    # Use rsync to copy files, preserving attributes and deleting extraneous files in APP_ROOT
    # Exclude the internal directories
    # Add trailing slash to WATCH_DIR to copy contents, not the directory itself
    "$rsync_binary" \
        -a --delete \
        --exclude '.sidecar/' \
        --exclude '.launcher/' \
        "${WATCH_DIR}"/* "${APP_ROOT}/"

    rsync_exit_code=$?
    if [ $rsync_exit_code -ne 0 ]; then
        echo "[code-sync] Error: rsync command failed with exit code ${rsync_exit_code}"
        return 1
    else
        echo "[code-sync] Successfully synced files to ${APP_ROOT}"
    fi
}

# Function to kill entire process group
kill_process_tree() {
    local pid=$1
    local signal=${2:-15}  # Default to SIGTERM
    
    if [ -f "$APP_PGID_FILE" ]; then
        pgid=$(cat "$APP_PGID_FILE")
        echo "[code-sync] Sending signal $signal to process group $pgid"
        kill -$signal -$pgid 2>/dev/null
        
        # Wait up to 5 seconds for graceful termination
        wait_time=0
        while kill -0 -$pgid 2>/dev/null && [ "$wait_time" -lt 5 ]; do
            sleep 1
            wait_time=$((wait_time + 1))
        done
        
        # Force kill if still running
        if kill -0 -$pgid 2>/dev/null; then
            echo "[code-sync] Process group did not stop gracefully. Sending SIGKILL."
            kill -9 -$pgid 2>/dev/null
        fi
    elif [ -n "$pid" ]; then
        echo "[code-sync] No process group ID found, falling back to PID $pid"
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
    PUSH_ID_FILE="${LAUNCHER_DIR}/push_id"
    if [ -f "$PUSH_ID_FILE" ]; then
        PUSH_ID_VALUE=$(cat "$PUSH_ID_FILE")
        if [ -n "$PUSH_ID_VALUE" ]; then
            export BIFROST_PUSH_ID="$PUSH_ID_VALUE"
            echo "[code-sync] Setting BIFROST_PUSH_ID='$BIFROST_PUSH_ID' from $PUSH_ID_FILE"
        else
            echo "[code-sync] Push ID file $PUSH_ID_FILE is empty, unsetting BIFROST_PUSH_ID"
            unset BIFROST_PUSH_ID
        fi
    else
        echo "[code-sync] Push ID file $PUSH_ID_FILE not found. Unsetting BIFROST_PUSH_ID."
        unset BIFROST_PUSH_ID
    fi

    # Source database environment variables if they exist
    DATABASE_ENV_FILE="${SIDECAR_DIR}/env.sh"
    if [ -f "$DATABASE_ENV_FILE" ]; then
        echo "[code-sync] Sourcing database environment variables from $DATABASE_ENV_FILE"
        . "$DATABASE_ENV_FILE"
    else
        echo "[code-sync] No database environment file found at $DATABASE_ENV_FILE"
    fi

    # Kill previous instance if it exists
    if [ -f "$APP_PID_FILE" ]; then
        old_pid=$(cat "$APP_PID_FILE")
        kill_process_tree "$old_pid"
    fi
    
    # Start the application in a new process group
    echo "[code-sync] Starting application with command: sh -c \"$*\""
    (
        # Use setsid to create a new process group
        # We need to track the new process group ID so we can kill it later
        setsid sh -c "$*" &
        
        APP_PID=$!
         # In a new session, PGID equals PID
        APP_PGID=$APP_PID
        
        # Save PIDs to files
        echo "$APP_PID" > "$APP_PID_FILE"
        echo "$APP_PGID" > "$APP_PGID_FILE"
        echo "[code-sync] Application started with PID $APP_PID, PGID $APP_PGID"
        
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
    echo "[code-sync] Received SIGHUP, restarting application"
    update_files
    shift # Remove the HUP signal from the arguments
    start_app "$@"
}

# Set up signal handlers
trap 'handle_sighup "$@"' HUP
trap 'echo "[code-sync] Received SIGTERM, shutting down"; kill_process_tree "$(cat "$APP_PID_FILE" 2>/dev/null)"; exit 0' TERM INT

# Keep running to handle signals
while true; do
    sleep 1
    # Check if app is still running
    if [ -f "$APP_PID_FILE" ] && [ -f "$APP_PGID_FILE" ]; then
        current_pid=$(cat "$APP_PID_FILE")
        current_pgid=$(cat "$APP_PGID_FILE")
        
        # Try to check the process group rather than just the PID
        if ! kill -0 -$current_pgid 2>/dev/null; then
            echo "[code-sync] Application process group at $current_pgid exited. Restarting."
            start_app "$@"
        fi
    else
        echo "[code-sync] PID/PGID file missing, restarting application"
        start_app "$@"
    fi
done