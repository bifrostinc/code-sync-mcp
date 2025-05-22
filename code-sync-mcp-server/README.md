# Bifrost MCP Server

This is a simple MCP server for interacting with Bifrost. It supports:

1. **Pushing/syncing to a Bifrost deployment**

   - The first push will take a bit, potentially 1-2 minutes as it clones and bootstraps the service.
   - Subsequent pushes should take a second or two, via a Websocket.

2. **Verifying that a deployment is working as expected**

   - This is very much WIP, but ideally runs some tests and verifies data via logs or other telemetry.

# Setup

## Cursor

Keeping it simple with examples, here's a few versions of setup handling:

- Production setup (what customers would use)
- Local development
- Production Dogfood (if we want to dogfood Bifrost-with-Bifrost). Only the verify tool will use the dogfood version, so you can safely push code changes

Cursor allows you to toggle servers on/off.

- The API key can be pulled from the `/api-keys` e.g. [in production](http://bifrost-frontend-alb-471270542.us-east-1.elb.amazonaws.com/api-keys)
- The `PATH-TO-REPO` should be the absolute path to the Bifrost repo.
- **Note if you make change to server code ensure you refresh the server so it loads the latest code**

```json
{
  "mcpServers": {
    "deployment-manager-prod": {
      "command": "uv",
      "args": [
        "--directory <PATH-TO-REPO>/mcp-server run src/bifrost_mcp/server.py"
      ],
      "env": {
        "BIFROST_API_KEY": "API-KEY",
        "BIFROST_API_URL": "https://bifrost-backend-production-dbbc.up.railway.app",
        "BIFROST_WS_API_URL": "wss://bifrost-backend-production-dbbc.up.railway.app"
      }
    },
    "deployment-manager-dev": {
      "command": "uv",
      "args": [
        "--directory <PATH-TO-REPO>/mcp-server run src/bifrost_mcp/server.py"
      ],
      "env": {
        "BIFROST_API_KEY": "API-KEY",
        "BIFROST_API_URL": "http://localhost:8000",
        "BIFROST_WS_API_URL": "ws://localhost:8000"
      }
    }
  }
}
```

## Client Development

We include an `cli.py` script for quick testing.

1. Create an `.env` file with a `BIFROST_API_KEY`
2. Run the script. It will expose the MCP tools as sub-commands. For example (if dogfooding the backend):

   ```bash
   uv run rsync_cli.py \
    --app-name bifrost-backend \
    --app-root "/Users/conorbranagan/dev/github.com/AIHacking2025/bifrost/backend" \
    --deployment-name conor
   ```
