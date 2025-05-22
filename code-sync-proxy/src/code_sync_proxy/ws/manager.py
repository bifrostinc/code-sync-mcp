import logging
from typing import Optional

from code_sync_proxy.ws.base_manager import BaseWebSocketManager
from code_sync_proxy.ws.standalone import (
    InMemoryPushRepository,
    AlwaysTrueDeploymentVerifier,
)
from code_sync_proxy.ws.connection_store import ConnectionStore, create_connection_store

log = logging.getLogger(__name__)

local_connection_store = create_connection_store()


class WebSocketManager(BaseWebSocketManager):
    """Standalone implementation of WebSocketManager for code-sync-proxy."""

    def __init__(self, connection_store: Optional[ConnectionStore] = None):
        """Initialize a standalone WebSocketManager with default implementations."""
        super().__init__(
            deployment_verifier=AlwaysTrueDeploymentVerifier(),
            push_repository=InMemoryPushRepository(),
            connection_store=connection_store or local_connection_store,
        )


# Create a default manager instance for the application to use
default_manager = WebSocketManager()
