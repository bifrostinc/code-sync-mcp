import asyncio
import logging
import os
from asyncio import Future, Lock
from typing import Optional
from contextlib import asynccontextmanager

import httpx
import websockets

from code_sync_mcp.push_handler import PushHandler, PushFuture, PushResult

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
READINESS_CHECKS_COUNT = 3
READINESS_CHECK_INTERVAL_SECONDS = 2

# Constants for retry logic, timeouts, etc.
MAX_CONNECT_RETRIES = 5
RECONNECT_DELAY = 5  # seconds


# Custom exception to signal push failure requires reconnect
class RequestRequiresReconnectError(Exception):
    """Custom exception to signal that a request operation requires a reconnect."""

    pass


# Custom exceptions for readiness check failures
class ReadinessCheckError(Exception):
    """Base exception for readiness check failures."""

    pass


class AuthenticationError(ReadinessCheckError):
    """Exception raised when API key is invalid or unauthorized."""

    pass


class SidecarNotConnectedError(ReadinessCheckError):
    """Exception raised when sidecar is not connected or there are network issues."""

    pass


class WebsocketClient:

    def __init__(self, app_id: str, deployment_id: str, app_root: str):
        self.app_root = app_root
        self.app_id = app_id
        self.deployment_id = deployment_id

        # API settings from environment
        self._api_key = os.getenv("BIFROST_API_KEY")
        if not self._api_key:
            raise ValueError("BIFROST_API_KEY is not set")

        self._base_url = os.getenv("BIFROST_API_URL", "http://localhost:8000")
        self._ws_base_url = os.getenv("BIFROST_WS_API_URL", "ws://localhost:8000")

        self._push_handler = PushHandler()

        # Connection state
        self._connection_timeout = (
            # Account for number of readiness checks and the time it takes to check readiness + buffer.
            READINESS_CHECKS_COUNT
            * READINESS_CHECK_INTERVAL_SECONDS
        ) + 1
        self._connected = asyncio.Event()
        self._connect_task: Optional[asyncio.Task] = None
        self._close_waiter: Optional[asyncio.Task] = None
        self._request_waiter: Optional[asyncio.Task] = None

        # Primitives to enqueue request, ensure only one request at a time
        self._request_available_event = asyncio.Event()
        self._api_call_lock = Lock()
        self._current_request_future: Optional[Future] = None

        # Set when we're going to close the connection
        self._close_requested = asyncio.Event()

        self._websocket: Optional[websockets.ClientConnection] = None

    def _get_readiness_uri(self) -> str:
        """Gets the appropriate readiness check URI based on mode."""
        return (
            f"{self._base_url}/api/v1/push/ide/{self.app_id}/{self.deployment_id}/ready"
        )

    def _get_websocket_uri(self) -> str:
        """Gets the appropriate WebSocket URI based on mode."""
        return f"{self._ws_base_url}/api/v1/push/ide/{self.app_id}/{self.deployment_id}"

    async def check_readiness(self) -> bool:
        """Checks if the backend considers this client's deployment environment ready."""
        headers = {"X-API-Key": self._api_key}
        uri = self._get_readiness_uri()
        log.info(f"Checking if code-sync proxy is ready: {uri}")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(uri, headers=headers, timeout=0.5)
                response.raise_for_status()
                is_ready = response.json().get("ready", False)
                if not is_ready:
                    log.warning(
                        f"Client {self.app_id}/{self.deployment_id} reported as not ready."
                    )
                return is_ready
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                log.error(
                    f"Authentication failed for {self.app_id}/{self.deployment_id}: "
                    f"Invalid API key. Status: {e.response.status_code}"
                )
                raise AuthenticationError("Invalid API key") from e
            else:
                log.warning(
                    f"HTTP error checking readiness for {self.app_id}/{self.deployment_id}: "
                    f"Status {e.response.status_code}. This may indicate the sidecar is not connected."
                )
                raise SidecarNotConnectedError(
                    f"HTTP error {e.response.status_code} - sidecar may not be connected"
                ) from e
        except httpx.RequestError as e:
            log.error(
                f"Network error checking readiness for {self.app_id}/{self.deployment_id}: {e}. "
                f"This may indicate the sidecar is not connected or there are network issues."
            )
            raise SidecarNotConnectedError(
                "Network error - sidecar may not be connected or there are network issues"
            ) from e
        except Exception:
            log.exception(
                f"Unexpected error checking readiness for {self.app_id}/{self.deployment_id}"
            )
            return False  # Assume not ready on unexpected errors

    async def connect(self):
        self._connect_task = asyncio.create_task(self._connect())
        try:
            await asyncio.wait_for(
                self._connected.wait(), timeout=self._connection_timeout
            )
        except asyncio.TimeoutError:
            if self._connect_task.done() and self._connect_task.exception() is not None:
                raise self._connect_task.exception()
            self._connected.set()
            raise RuntimeError("Timed out waiting for connection to be established")
        except Exception as e:
            raise RuntimeError(f"Error connecting to code-sync proxy: {e}") from e

    async def _connect(self):
        """Main function for the IDE client connection and request handling."""
        await self._ensure_readiness()
        await self._run_connection_loop()

    async def _ensure_readiness(self):
        """Ensure the deployment is ready before attempting connection."""
        last_error = None
        is_ready = False

        for attempt in range(READINESS_CHECKS_COUNT):
            try:
                is_ready = await self.check_readiness()
                if is_ready:
                    return
                log.info(
                    f"Proxy not ready, waiting {READINESS_CHECK_INTERVAL_SECONDS} seconds and checking again"
                )
            except AuthenticationError as e:
                # Authentication errors are immediate failures, don't retry
                log.error(f"Authentication error on attempt {attempt + 1}: {e}")
                raise RuntimeError(f"Authentication failed: {e}") from e
            except SidecarNotConnectedError as e:
                # Sidecar connection errors should be retried
                last_error = e
                log.warning(f"Sidecar connection error on attempt {attempt + 1}: {e}")
                if attempt < READINESS_CHECKS_COUNT - 1:
                    log.info(
                        f"Retrying readiness check in {READINESS_CHECK_INTERVAL_SECONDS} seconds..."
                    )
                    await asyncio.sleep(READINESS_CHECK_INTERVAL_SECONDS)
                continue
            except Exception as e:
                # Other unexpected errors
                last_error = e
                log.error(
                    f"Unexpected error checking readiness on attempt {attempt + 1}: {e}"
                )
                if attempt < READINESS_CHECKS_COUNT - 1:
                    log.info(
                        f"Retrying readiness check in {READINESS_CHECK_INTERVAL_SECONDS} seconds..."
                    )
                    await asyncio.sleep(READINESS_CHECK_INTERVAL_SECONDS)
                continue

            # Only sleep if we didn't break out of the loop and not on the last attempt
            if attempt < READINESS_CHECKS_COUNT - 1:
                await asyncio.sleep(READINESS_CHECK_INTERVAL_SECONDS)

        # If we get here, readiness check failed
        self._raise_readiness_error(last_error)

    def _raise_readiness_error(self, last_error):
        """Raise appropriate error based on the last readiness check failure."""
        if isinstance(last_error, SidecarNotConnectedError):
            raise RuntimeError(
                f"Deployment not available after {READINESS_CHECKS_COUNT} readiness checks: "
                f"{last_error}. Please ensure the sidecar is running and connected."
            ) from last_error
        elif last_error:
            raise RuntimeError(
                f"Deployment not available after {READINESS_CHECKS_COUNT} readiness checks: {last_error}"
            ) from last_error
        else:
            raise RuntimeError(
                f"Deployment not available after {READINESS_CHECKS_COUNT} readiness checks - proxy reported not ready"
            )

    async def _run_connection_loop(self):
        """Run the main connection loop with retry logic."""
        uri = self._get_websocket_uri()
        headers = {"X-API-Key": self._api_key}
        log.info(f"Creating WebSocket connection to: {uri}")

        retries = 0
        while not self._close_requested.is_set() and retries < MAX_CONNECT_RETRIES:
            try:
                async with websockets.connect(
                    uri, additional_headers=headers
                ) as websocket:
                    await self._handle_websocket_connection(websocket)

                    # If we get here, close was requested during connection
                    if self._close_requested.is_set():
                        break

            except (
                websockets.exceptions.ConnectionClosedError,
                OSError,
                ConnectionRefusedError,
            ) as e:
                self._handle_connection_error(e)
            except Exception as e:
                self._handle_unexpected_error(e)
            finally:
                self._connected.clear()

            # Wait before retrying connection only if not closing
            if not self._close_requested.is_set():
                await asyncio.sleep(RECONNECT_DELAY)
                retries += 1

        await self._cleanup_after_connection_loop()

    async def _handle_websocket_connection(self, websocket):
        """Handle an active WebSocket connection and process requests."""
        self._websocket = websocket
        log.info("Connected to code-sync proxy")
        self._connected.set()

        # Create tasks for waiting on request or close
        self._request_waiter = asyncio.create_task(self._request_available_event.wait())
        self._close_waiter = asyncio.create_task(self._close_requested.wait())

        try:
            while not self._close_requested.is_set():
                done, pending = await asyncio.wait(
                    [self._request_waiter, self._close_waiter],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if self._close_waiter in done:
                    log.info("Close requested, shutting down connection.")
                    break

                if self._request_waiter in done:
                    should_reconnect = await self._process_request(websocket)
                    if should_reconnect:
                        break
        finally:
            # Cleanup pending tasks
            for task in [self._request_waiter, self._close_waiter]:
                if task and not task.done():
                    task.cancel()

    async def _process_request(self, websocket) -> bool:
        """Process a single request. Returns True if reconnection is needed."""
        self._request_available_event.clear()
        # Recreate request_waiter for the next wait *before* potentially long request
        self._request_waiter = asyncio.create_task(self._request_available_event.wait())

        # Check if there's an active request to process
        active_future = self._current_request_future
        if active_future and not active_future.done():
            log.info("Processing active request...")
            try:
                await self._dispatch_request_handler(websocket, active_future)
            except RequestRequiresReconnectError:
                # Request failed due to connection issue, need to reconnect and retry.
                log.warning(
                    "Request requires reconnect. Breaking inner loop to reconnect."
                )
                self._request_available_event.set()
                return True  # Signal reconnection needed
            except Exception as e:
                log.exception("Error during request processing.")
                if not active_future.done():
                    active_future.set_exception(e)

        return False  # No reconnection needed

    def _handle_connection_error(self, e):
        """Handle connection-related errors."""
        log.warning(
            f"Connection failed or closed: {e}. Retrying in {RECONNECT_DELAY} seconds...",
            exc_info=True,
        )
        self._fail_active_request(e)

    def _handle_unexpected_error(self, e):
        """Handle unexpected errors during connection."""
        log.exception(
            f"{e} An unexpected error occurred in the connect loop. Retrying in {RECONNECT_DELAY} seconds..."
        )
        self._fail_active_request(
            RuntimeError("Unexpected connection loop error during push")
        )

    def _fail_active_request(self, exception):
        """Fail the current active request with the given exception."""
        if self._current_request_future and not self._current_request_future.done():
            log.warning(
                "Setting exception for active request future due to connection error."
            )
            self._current_request_future.set_exception(exception)
            self._current_request_future = None

    async def _cleanup_after_connection_loop(self):
        """Clean up after the connection loop exits."""
        log.info("WebsocketClient connection loop finished.")

        # Fail any potentially active push future if the loop exits cleanly due to close request
        if self._current_request_future and not self._current_request_future.done():
            log.info(
                "Failing active request future because connection loop is exiting."
            )
            self._current_request_future.set_exception(
                RuntimeError("Client closed during active request")
            )

        # Explicitly close the websocket if it's still open and close was requested
        if self._websocket:
            log.info("Closing WebSocket connection finally.")
            await self._websocket.close()
            self._websocket = None

    async def push(self, code_diff: str, change_description: str) -> PushResult:
        """Requests a push and waits for it to complete or fail. Ensures only one push runs at a time."""
        async with self._with_api_lock():
            # Create and store the future for this push operation
            push_future: PushFuture = PushFuture(code_diff, change_description)
            self._current_request_future = push_future
            log.info(f"Triggering push (Future ID: {id(push_future)})...")
            self._request_available_event.set()
            try:
                result = await push_future
                log.info(f"Push completed successfully (Future ID: {id(push_future)}).")
                self._current_request_future = None
                return result
            except Exception as e:
                log.error(f"Push failed (Future ID: {id(push_future)}): {e}")
                self._current_request_future = None
                raise

    async def close(self):
        log.info("Close requested for WebsocketClient.")
        self._close_requested.set()

        # If a request is currently in progress, signal its future that we are closing
        # Do not wait for the lock here, just check and set exception if needed
        if self._current_request_future and not self._current_request_future.done():
            log.warning(
                f"Setting exception for active request future {id(self._current_request_future)} due to client close."
            )
            self._current_request_future.set_exception(
                RuntimeError("WebsocketClient closed during active request")
            )

        # Cancel the connect task if it's running
        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
            try:
                await self._connect_task  # Wait for cancellation to complete
            except asyncio.CancelledError:
                log.info("Connect task cancelled.")
            except Exception:
                # Log details if cancellation itself caused an issue
                log.exception(
                    "Error waiting for connect task cancellation during close."
                )

        # Ensure the websocket is closed if connect didn't handle it
        # (This is also handled in connect's finally block, but good to be defensive)
        if self._websocket:
            log.info("Ensuring WebSocket is closed during client close.")
            await self._websocket.close()
            self._websocket = None

    @asynccontextmanager
    async def _with_api_lock(self):
        """Acquires the API call lock and context manager."""
        if self._close_requested.is_set():
            raise RuntimeError("Cannot push, WebsocketClient is closing or closed.")
        if not self._connect_task or self._connect_task.done():
            raise RuntimeError(
                "Cannot push, WebsocketClient connection task is not running."
            )

        async with self._api_call_lock:
            if self._close_requested.is_set():
                raise RuntimeError(
                    "Cannot run request, WebsocketClient closed while waiting for lock."
                )
            if self._current_request_future is not None:
                log.error(
                    "New request initiated while another request is already in progress. This indicates a logic issue."
                )
                raise RuntimeError("Another request is already in progress.")

            log.info(f"API call lock acquired for deployment_id:{self.deployment_id}.")

            yield

    async def _dispatch_request_handler(
        self, websocket: websockets.ClientConnection, request_future: Future
    ):
        """Dispatches the request to the appropriate handler based on future type."""
        log.info(f"Dispatching request for future type: {type(request_future)}")
        try:
            if isinstance(request_future, PushFuture):
                await self._push_handler.handle_push_request(
                    websocket, self.app_root, request_future
                )
            else:
                err_msg = f"Unknown request future type: {type(request_future)}"
                log.error(err_msg)
                if not request_future.done():
                    request_future.set_exception(TypeError(err_msg))
        except asyncio.TimeoutError:
            log.error("Timeout waiting for response from proxy.")
            if not request_future.done():
                request_future.set_exception(
                    RequestRequiresReconnectError("Timeout waiting for push response")
                )
            raise RequestRequiresReconnectError(
                "Timeout waiting for response from proxy"
            )
        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"Connection closed during request send: {e}")
            if not request_future.done():
                request_future.set_exception(
                    RequestRequiresReconnectError(
                        "Connection closed during request send"
                    )
                )
            raise RequestRequiresReconnectError(
                "Connection closed during request send"
            ) from e
        except Exception as e:
            # Broad exception catch for anything else in the main try block of send_push_batch
            log.exception(
                f"Unexpected error in dispatch_request_handler for future {id(request_future)}: {e}"
            )
            if not request_future.done():
                request_future.set_exception(e)
            raise
