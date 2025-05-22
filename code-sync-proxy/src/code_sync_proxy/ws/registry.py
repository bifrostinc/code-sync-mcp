from dataclasses import dataclass
from typing import Dict, Optional, ClassVar
from enum import Enum

from fastapi import WebSocket

from code_sync_proxy.config import settings

STANDALONE_ID = "standalone"


@dataclass
class ConnectionKey:
    app_id: str
    deployment_id: str
    org_id: Optional[str] = None
    user_id: Optional[str] = None
    module_name: ClassVar[str] = "code_sync_proxy.ws.manager"

    def __post_init__(self):
        # Ensure we have non-None values for serialization
        self.org_id = self.org_id or STANDALONE_ID
        self.user_id = self.user_id or STANDALONE_ID

    def __hash__(self) -> int:
        return hash((self.org_id, self.user_id, self.app_id, self.deployment_id))

    def to_redis_key(self) -> str:
        return f"{self.org_id}:{self.user_id}:{self.app_id}:{self.deployment_id}"

    def log_fields(self) -> Dict[str, str]:
        """Return fields to include in log messages."""
        fields = {
            "module_name": self.module_name,
            "app_id": self.app_id,
            "deployment_id": self.deployment_id,
            "worker_id": settings.worker_id,
        }

        # Add org_id and user_id if they're not the default "standalone" values
        if self.org_id != STANDALONE_ID:
            fields["org_id"] = self.org_id
        if self.user_id != STANDALONE_ID:
            fields["user_id"] = self.user_id

        return fields


class ConnectionType(Enum):
    SIDECAR = "sidecar"
    IDE = "ide"


class ConnectionRegistry:
    def __init__(self):
        self.connections: dict[tuple[ConnectionType, ConnectionKey], WebSocket] = {}

    def register_connection(
        self, conn_type: ConnectionType, conn_key: ConnectionKey, websocket: WebSocket
    ):
        key = (conn_type, conn_key)
        if key not in self.connections:
            self.connections[key] = websocket
        else:
            raise ValueError(f"Connection {key} already registered")

    def deregister_connection(self, conn_type: ConnectionType, conn_key: ConnectionKey):
        key = (conn_type, conn_key)
        if key in self.connections:
            del self.connections[key]

    def get_connection(
        self, conn_type: ConnectionType, conn_key: ConnectionKey
    ) -> Optional[WebSocket]:
        key = (conn_type, conn_key)
        return self.connections.get(key)
