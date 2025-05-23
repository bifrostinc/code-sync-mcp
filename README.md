# Code Sync MCP Server

**Hot reload your remote containerized applications directly from your IDE using MCP (Model Context Protocol).**

The Code Sync MCP architecture bridges the gap between local development and remote containers - edit code in your local editor and instantly see changes reflected in containers running anywhere (staging, development clusters, cloud environments, or even production). No more manual deployments or waiting for CI/CD pipelines just to test a small change.

## What It Does

- **Instant remote sync**: Changes in your local editor appear immediately in containers running anywhere
- **Multi-environment support**: Each developer can have their own isolated environment
- **Minimal container changes**: Just wrap your existing entrypoint—no Dockerfile rewrites needed
- **IDE integration**: Works through MCP tools in editors like Cursor

## Demo

The included [docker-compose.yaml](./docker-compose.yml) runs everything locally with a demo app. See [Try It Out](#try-it-out) for instructions on how to run this locally.

<video src="https://github.com/user-attachments/assets/eb6c267d-16c5-435f-9179-be13aad13456" width="100"></video>

_This demo shows introducing a bug in a health check, seeing it break immediately, then fixing it in real-time._

## How It Works

When you make a code change in your editor:

1. **MCP tool call** triggers from your editor (e.g., Cursor)
2. **Local rsync** generates a patch of your changes
3. **Proxy** routes the patch to the correct container environment
4. **Sidecar** applies changes to a shared volume
5. **Your app** gets restarted automatically with the new code

![Architecture diagram](./images/arch.png)

## Setup Guide

### Prerequisites

- Docker/container environment
- Editor with MCP support (like Cursor)
- An API key for securing connections

### 1. Deploy the Proxy (Once per Organization)

The proxy is a central websocket server that routes code changes to the right containers.

**Deploy [code-sync-proxy](./code-sync-proxy)** with:

```bash
PROXY_API_KEY=your-secret-key-here
```

You only need **one proxy** for all your applications and developers.

### 2. Configure Your Application (Per App/Deployment)

For each app deployment, you need two changes:

#### A) Modify your container entrypoint

Replace your existing entrypoint with this wrapper that waits for the sync system:

```bash
# Simple one-liner approach (recommended)
sh -c "while [ ! -f /app-files/.sidecar/rsync-launcher.sh ]; do echo 'Waiting for sync...'; sleep 1; done && /app-files/.sidecar/rsync-launcher.sh 'YOUR_ORIGINAL_COMMAND_HERE'"
```

Or use the [provided script template](./demo-app/code-sync-entrypoint.sh).

#### B) Add the sidecar container

Deploy [code-sync-sidecar](https://hub.docker.com/r/bifrostinc/code-sync-sidecar) container alongside your app with these environment variables:

```bash
BIFROST_API_URL=http://your-proxy-url
BIFROST_API_KEY=your-secret-key-here   # Same as proxy
BIFROST_APP_ID=my-app                  # Unique app identifier
BIFROST_DEPLOYMENT_ID=dev-john         # Unique deployment name
```

#### C) [If Required] Ensure you app has permissions for syncing (if not running as root)

Add to your Dockerfile:

```dockerfile
RUN useradd -m appuser
RUN chown -R appuser:appuser /app
USER appuser
```

### 3. Configure Your Editor

For **Cursor**, add this to your MCP settings:

```json
{
  "mcpServers": {
    "code-sync": {
      "command": "uvx code-sync-mcp",
      "env": {
        "BIFROST_API_KEY": "your-secret-key-here",
        "BIFROST_WS_API_URL": "ws://your-proxy-url",
        "BIFROST_API_URL": "http://your-proxy-url"
      }
    }
  }
}
```

You'll see these tools become available:
![Cursor MCP tools](./images/cursor-mcp.png)

Then you need to add a `.bifrost.json` file to your app's root:

```
{
    "app_id": "my-app",
    "deployment_id": "dev-john"
}
```

## Usage

Once set up, use the `push_changes` tool in your editor to sync your local code changes to any running container. The system respects your `.gitignore` file automatically.

## Architecture Deep Dive

The system has four main components:

### [code-sync-mcp-server](./code-sync-mcp-server/)

- Runs locally in your editor
- Exposes `push_changes` MCP tool
- Uses `rsync` to efficiently detect and package changes
- Respects `.gitignore` rules

### [code-sync-proxy](./code-sync-proxy/)

- Central websocket server (FastAPI-based)
- Routes change batches to correct sidecar instances
- Handles authentication and connection management
- One instance serves all apps and developers

### [code-sync-sidecar](./code-sync-sidecar/)

- Runs alongside each application container
- Receives change batches via websocket
- Syncs files to shared volume with main app
- Sends `SIGHUP` to trigger app restart

### [rsync-launcher.sh](./code-sync-sidecar/launcher-script/rsync-launcher.sh)

- Wrapper script for your application
- Syncs files from shared volume into app directory
- Handles graceful restarts on file changes
- Minimal modification to existing containers

## Key Benefits

- **Fast iteration**: See changes instantly without rebuilding containers
- **Multiple environments**: Each developer gets isolated sync targets
- **Minimal invasiveness**: Only requires entrypoint wrapper
- **Efficient**: Uses rsync for incremental updates only
- **Secure**: API key authentication between all components

## Try It Out

1. Clone this repo
2. Run `docker-compose up`
3. Configure Cursor with the local proxy settings
4. Make changes to files in `demo-app/` and push them!

The demo app will immediately reflect your changes.
