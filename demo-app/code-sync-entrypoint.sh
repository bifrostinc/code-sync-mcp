#!/bin/bash
set -e

echo "Waiting for sidecar launcher script..."
while [ ! -f /app-files/.sidecar/rsync-launcher.sh ]; do 
    echo "Waiting for launcher script..."
    sleep 1
done

echo "Starting application with sidecar..."
exec /app-files/.sidecar/rsync-launcher.sh 'fastapi run app.py --host 0.0.0.0 --port 8000'