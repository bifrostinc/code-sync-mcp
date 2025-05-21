from typing import Optional
import abc

import redis
import logging

from code_sync_proxy.config import settings
from code_sync_proxy.ws.registry import ConnectionKey, ConnectionType

log = logging.getLogger(__name__)


class ConnectionStore(abc.ABC):
    """A connections store manages a mapping of (connection_type, connection_key) to worker_id
    for the purpose of routing messages between workers and clients.
    """

    @abc.abstractmethod
    def register_connection(
        self,
        conn_type: ConnectionType,
        conn_key: ConnectionKey,
        worker_id: str = settings.worker_id,
    ) -> None:
        pass

    @abc.abstractmethod
    def deregister_connection(
        self,
        conn_type: ConnectionType,
        conn_key: ConnectionKey,
        worker_id: str = settings.worker_id,
    ) -> None:
        pass

    @abc.abstractmethod
    def get_worker_id(
        self, conn_type: ConnectionType, conn_key: ConnectionKey
    ) -> Optional[str]:
        pass


class LocalConnectionStore(ConnectionStore):
    """The local connection store is a simple in-memory store for testing purposes."""

    def __init__(self):
        self.connections: dict[tuple[ConnectionType, ConnectionKey], str] = {}

    def register_connection(
        self,
        conn_type: ConnectionType,
        conn_key: ConnectionKey,
        worker_id: str = settings.worker_id,
    ) -> None:
        self.connections[(conn_type, conn_key)] = worker_id

    def deregister_connection(
        self,
        conn_type: ConnectionType,
        conn_key: ConnectionKey,
        worker_id: str = settings.worker_id,
    ) -> None:
        current_worker = self.connections.get((conn_type, conn_key))
        if current_worker == worker_id:
            del self.connections[(conn_type, conn_key)]
        elif current_worker:
            log.warning(
                f"Worker {worker_id} tried to remove local connection for {conn_key.log_fields()}, but it was held by {current_worker}"
            )

    def get_worker_id(
        self, conn_type: ConnectionType, conn_key: ConnectionKey
    ) -> Optional[str]:
        return self.connections.get((conn_type, conn_key))


class RedisConnectionStore(ConnectionStore):
    SIDECAR_PREFIX = "ws_code_sync_proxy:sidecar:"
    IDE_PREFIX = "ws_code_sync_proxy:ide:"

    def __init__(self):
        redis_host = settings.redis_host
        redis_port = settings.redis_port
        redis_db = settings.redis_db
        redis_password = settings.redis_password

        self.client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            decode_responses=True,
        )
        log.info(
            f"Redis connection store initialized with Redis host {redis_host}:{redis_port}, db {redis_db}"
        )

    def _get_key(self, conn_type: ConnectionType, conn_key: ConnectionKey) -> str:
        if conn_type == ConnectionType.SIDECAR:
            return f"{self.SIDECAR_PREFIX}{conn_key.to_redis_key()}"
        elif conn_type == ConnectionType.IDE:
            return f"{self.IDE_PREFIX}{conn_key.to_redis_key()}"
        else:
            raise ValueError(f"Invalid connection type: {conn_type}")

    def register_connection(
        self,
        conn_type: ConnectionType,
        conn_key: ConnectionKey,
        worker_id: str = settings.worker_id,
    ) -> None:
        key = self._get_key(conn_type, conn_key)
        self.client.set(key, worker_id, ex=settings.websocket_redis_ttl_seconds)

    def deregister_connection(
        self,
        conn_type: ConnectionType,
        conn_key: ConnectionKey,
        worker_id: str = settings.worker_id,
    ) -> None:
        key = self._get_key(conn_type, conn_key)
        current_holder = self.client.get(key)
        if current_holder == worker_id:
            self.client.delete(key)
            log.info(f"Connection {key} deregistered from Redis by worker {worker_id}")
        elif current_holder:
            log.warning(
                f"Worker {worker_id} tried to remove {key} from Redis, but it was held by {current_holder}"
            )

    def get_worker_id(
        self, conn_type: ConnectionType, conn_key: ConnectionKey
    ) -> Optional[str]:
        key = self._get_key(conn_type, conn_key)
        return self.client.get(key)


# Factory function to create the right connection store based on settings
def create_connection_store() -> ConnectionStore:
    """Factory function to create the appropriate connection store based on settings."""
    if settings.redis_enabled:
        return RedisConnectionStore()
    else:
        return LocalConnectionStore()
