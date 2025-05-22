"""
Code Sync MCP Server using Rsync for IDE-sidecar communication
"""

import logging
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP, Context

from code_sync_mcp.client_manager import ClientManager

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    client_manager: ClientManager


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage application lifecycle with type-safe context"""
    # Initialize on startup
    log.info("Initializing client manager")
    client_manager = ClientManager()
    try:
        yield AppContext(client_manager=client_manager)
    finally:
        # Cleanup on shutdown
        await client_manager.close_all()


mcp = FastMCP("Code Sync MCP Server", lifespan=app_lifespan)


@mcp.tool()
async def push_changes(
    ctx: Context,
    app_id: str,
    deployment_id: str,
    app_root: str,
    code_diff: str,
    change_description: str,
) -> dict[str, str]:
    """Push the application at app_root to the deployment

    Requires a .bifrost.json file in the app root.

    Args:
        app_id: The ID of the application to deploy. This must be read from the .bifrost.json file in the app root.
        deployment_id: The id of the deployment environment to deploy to from the .bifrost.json file in the app root.
        app_root: The root of the repository to deploy. This is either explicitlydefined in the .bifrost.json file or the full absolute path to the .bifrost.json file.
        code_diff: The code changes to deploy as a diff formatted string.
        change_description: The description of the changes to the deployment. This should come from the prompt and/or a summary of the change since last push.
    """
    manager: ClientManager = ctx.request_context.lifespan_context.client_manager
    try:
        client = await manager.get_or_create(app_id, app_root, deployment_id)
        result = await client.push(code_diff, change_description)
        return f"Pushed code to deployment {deployment_id} for {app_id}, status: {result.status}, push_id: {result.push_id}"
    except Exception as e:
        log.error(f"Error pushing code to deployment: {e}, type: {type(e)}")
        raise


def main():
    mcp.run()


if __name__ == "__main__":
    main()
