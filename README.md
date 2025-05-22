# Code Sync MCP Server

This repository provides a series of apps which allow for fast hot reloading of an application via MCP.

The goal is to work with minimal changes to your application's container, simply wrapping the entry point in a wrapper script that's provided by the sidecar. See [demo-app/code-sync-entrypoint.sh](./demo-app/code-sync-entrypoint.sh) for an example.

## Demo

We include a [docker-compose.yaml](./docker-compose.yml) file which runs all the necessary services locally including a demo application.

This demo video walks through a scenario of introducing an error in the health check, seeing the behavior change, and undoing the change.

<video src="https://github.com/user-attachments/assets/e3d89ec6-3d0a-472d-98f1-5317c118e310" width="100"></video>


## Approach

Code is synced to a running container by:

1. Making a `push_changes` tool call (via MCP) to the `code-sync-mcp-server`. This kicks off a rsync call to sync any local changes with the last known state (or all files, for the initial rsync). This will generate a batch file sent to the `code-sync-proxy`.
2. The `code-sync-proxy` will receive the batch and proxy it to the appropriate `code-sync-sidecar` connection.
3. The `code-sync-sidecar` will sync its local state with the incoming batch and it will send a `SIGHUP` to the application.
4. Finally, using the wrapping launcher script, the application will sync files from the shared volume into the application's directory.

## Architecture

This image shows the architecture of the components involved in code syncing.

![architecture](./images/arch.png)

There are 4 main components:

- [code-sync-mcp-server](./code-sync-mcp-server/) runs on your local machine and interacts with an editor like Cursor. A `push_changes` tool call is exposed. Locally we will run `rsync` to generate and send a batch file with changes, account for the `.gitignore` file.
- [code-sync-sidecar](./code-sync-sidecar/) runs alongside your application (e.g. within the same Kubernetes pod or as an additional container in ECS). When it receives a batch of changes files are synced locally to a shared volume between the sidecar and your application.
- [code-sync-proxy](./code-sync-proxy/) sits between the MCP server and the sidecar, acting as a websocket proxy to pass through batches of changes.
- [rsync-launcher.sh](./code-sync-sidecar/launcher-script/rsync-launcher.sh) script which will wrap your application. This manages syncing between the shared sidecar volume and the local application's state. This ensures minimal changes to your application's container - you simply change the entrypoint (as in [demo-app/code-sync-entrypoint.sh](./demo-app/code-sync-entrypoint.sh)).
