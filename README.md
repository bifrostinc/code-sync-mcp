# Code Sync Sidecar

This sidecar sits alongside apps and managing getting code into the right place.

There's a few steps to the process.

1. Open a websocket to the Bifrost API and await incoming rsync batches.
2. Upon receiving a batch, sync it with the local state.
3. Send a SIGHUP to the "main" app via the [rsync-launcher](launcher-script/rsync-launcher.sh) wrapper.
4. The launcher will use `rync` between the sidecar's volume and the local app and then restart the main app.
