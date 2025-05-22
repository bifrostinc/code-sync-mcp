import logging
import asyncio
from typing import Optional

from code_sync_mcp.websocket_client import WebsocketClient

log = logging.getLogger(__name__)


class ClientManager:
    _active_clients: dict[str, WebsocketClient] = {}
    _readiness_checker: Optional[asyncio.Task] = None
    _stop_readiness_checker = asyncio.Event()
    _readiness_check_interval_seconds = 60

    @classmethod
    async def _ensure_readiness_checker_running(cls):
        """Starts the readiness checker task if it's not already running."""
        if cls._readiness_checker is not None and not cls._readiness_checker.done():
            return

        if cls._readiness_checker and cls._readiness_checker.done():
            try:
                cls._readiness_checker.result()
            except asyncio.CancelledError:
                log.info("Previous readiness checker task was cancelled.")
            except Exception:
                log.exception("Readiness checker task failed unexpectedly. Restarting.")

        log.info("Starting background readiness checker task.")
        cls._stop_readiness_checker.clear()
        cls._readiness_checker = asyncio.create_task(cls._run_readiness_checker())

    @classmethod
    async def _run_readiness_checker(cls):
        """Periodically checks the readiness of all active clients."""
        while not cls._stop_readiness_checker.is_set():
            try:
                # Wait for the event or timeout (sleep)
                try:
                    await asyncio.wait_for(
                        cls._stop_readiness_checker.wait(),
                        timeout=cls._readiness_check_interval_seconds,
                    )
                    # If wait() returned, it means the event was set.
                    if cls._stop_readiness_checker.is_set():
                        log.info("Stop event set, exiting readiness checker.")
                        break
                except asyncio.TimeoutError:
                    # This is expected, means the sleep interval passed
                    pass

                if not cls._active_clients:
                    log.debug("No active clients, skipping readiness check.")
                    continue

                log.info(
                    f"Running readiness check for {len(cls._active_clients)} clients..."
                )
                # Iterate over a copy of keys to allow modification during iteration
                client_keys_to_check = list(cls._active_clients.keys())

                tasks = []
                clients_to_remove = []

                async def check_client(key):
                    client = cls._active_clients.get(key)
                    if client is None:
                        return
                    try:
                        is_ready = await client.check_readiness()
                        if not is_ready:
                            log.warning(
                                f"Client {key} is not ready. Closing and removing."
                            )
                            clients_to_remove.append(key)
                    except Exception as e:
                        log.error(
                            f"Error during readiness check for client {key}: {e}. Removing."
                        )
                        clients_to_remove.append(key)

                for key in client_keys_to_check:
                    # Check again if client still exists before creating task
                    if key in cls._active_clients:
                        tasks.append(asyncio.create_task(check_client(key)))

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

                # Remove and close clients marked for removal
                close_tasks = []
                for key in clients_to_remove:
                    client = cls._active_clients.pop(key, None)
                    if client:
                        log.info(f"Initiating close for non-ready client {key}.")
                        close_tasks.append(asyncio.create_task(client.close()))

                if close_tasks:
                    results = await asyncio.gather(*close_tasks, return_exceptions=True)
                    for key, result in zip(clients_to_remove, results):
                        if isinstance(result, Exception):
                            log.error(f"Error closing non-ready client {key}: {result}")

            except asyncio.CancelledError:
                log.info("Readiness checker task cancelled.")
                break
            except Exception:
                log.exception("Error in readiness checker loop. Continuing...")
                # Add a small delay before retrying after an unexpected error
                await asyncio.sleep(5)

    @classmethod
    async def create(
        cls, app_id: str, app_root: str, deployment_id: str
    ) -> WebsocketClient:
        await cls._ensure_readiness_checker_running()
        key = (app_id, deployment_id)
        if key in cls._active_clients:
            raise ValueError(f"Client for {key} already exists")

        client = WebsocketClient(app_id, deployment_id, app_root)
        cls._active_clients[key] = client
        log.info(f"stored client {key} in {cls._active_clients}")

        # Start the connection task
        await client.connect()
        return client

    @classmethod
    async def get(cls, app_id: str, deployment_id: str) -> WebsocketClient:
        key = (app_id, deployment_id)
        if key not in cls._active_clients:
            raise ValueError(f"Client for {key} does not exist")
        return cls._active_clients[key]

    @classmethod
    async def get_or_create(
        cls, app_id: str, app_root: str, deployment_id: str
    ) -> WebsocketClient:
        await cls._ensure_readiness_checker_running()
        key = (app_id, deployment_id)
        if key not in cls._active_clients:
            return await cls.create(app_id, app_root, deployment_id)
        return cls._active_clients[key]

    @classmethod
    async def close_all(cls):
        close_tasks = [client.close() for client in cls._active_clients.values()]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        cls._active_clients.clear()

        # Stop the readiness checker task
        if cls._readiness_checker and not cls._readiness_checker.done():
            log.info("Stopping readiness checker task.")
            cls._stop_readiness_checker.set()
            try:
                await asyncio.wait_for(cls._readiness_checker, timeout=10.0)
            except asyncio.TimeoutError:
                log.warning(
                    "Readiness checker task did not stop gracefully within timeout."
                )
                cls._readiness_checker.cancel()
            except Exception:
                log.exception("Error stopping readiness checker task.")
            finally:
                cls._readiness_checker = None
        log.info("All clients closed")
