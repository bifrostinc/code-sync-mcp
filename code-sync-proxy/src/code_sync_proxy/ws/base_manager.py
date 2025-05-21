import logging
from typing import Optional, Dict, Protocol

from fastapi import WebSocket, WebSocketDisconnect
from google.protobuf import json_format

from code_sync_proxy.pb import ws_pb2
from code_sync_proxy.config import settings
from code_sync_proxy.ws.registry import (
    ConnectionKey,
    ConnectionType,
    ConnectionRegistry,
)
from code_sync_proxy.ws.connection_store import ConnectionStore, create_connection_store
from code_sync_proxy.ws.message import MessageFactory
from code_sync_proxy.ws.interfaces import (
    PushRepository,
    DeploymentVerifier,
    VerificationRunner,
)

log = logging.getLogger(__name__)


# Protocol definitions for better type hints
class MessageHandler(Protocol):
    async def __call__(
        self,
        conn_key: ConnectionKey,
        message: ws_pb2.WebsocketMessage,
    ) -> None: ...


# Message type enums for cleaner reference
PushStatusPb = ws_pb2.PushResponse.PushStatus
VerificationStatusPb = ws_pb2.VerificationResponse.VerificationStatus


async def send_websocket_message(
    websocket: WebSocket, message: ws_pb2.WebsocketMessage
) -> None:
    await websocket.send_bytes(message.SerializeToString())


async def recv_websocket_message(websocket: WebSocket) -> ws_pb2.WebsocketMessage:
    data = await websocket.receive_bytes()
    try:
        message = ws_pb2.WebsocketMessage()
        message.ParseFromString(data)
        return message
    except Exception as e:
        raise ValueError(f"Received invalid websocket message: {e}")


class BaseWebSocketManager:
    """Base WebSocket manager class with pluggable components for deployment verification and push storage."""

    _local_registry: ConnectionRegistry = ConnectionRegistry()

    def __init__(
        self,
        deployment_verifier: DeploymentVerifier,
        push_repository: PushRepository,
        verification_runner: VerificationRunner,
        connection_store: Optional[ConnectionStore] = None,
    ):
        self.deployment_verifier = deployment_verifier
        self.push_repo = push_repository
        self.verification_runner = verification_runner

        # Connection state
        self.registry = self._local_registry
        self.cx_store: ConnectionStore = connection_store or create_connection_store()

        # Handlers for each message type
        self._message_handlers: Dict[
            ws_pb2.WebsocketMessage.MessageType, MessageHandler
        ] = {
            ws_pb2.WebsocketMessage.MessageType.PUSH_REQUEST: self._handle_push_request,
            ws_pb2.WebsocketMessage.MessageType.PUSH_RESPONSE: self._handle_push_response,
            ws_pb2.WebsocketMessage.MessageType.VERIFICATION_REQUEST: self._handle_verification_request,
        }

    def _make_key(
        self,
        app_id: str,
        deployment_id: str,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> ConnectionKey:
        """Create a connection key from app and deployment IDs."""
        return ConnectionKey(
            org_id=org_id,
            user_id=user_id,
            app_id=app_id,
            deployment_id=deployment_id,
        )

    def is_sidecar_ready(
        self,
        app_id: str,
        deployment_id: str,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Check if a sidecar connection is active for the given app/deployment."""
        key = self._make_key(app_id, deployment_id, org_id, user_id)
        worker_id = self.cx_store.get_worker_id(ConnectionType.SIDECAR, key)
        is_connected = worker_id is not None

        if not is_connected:
            log.warning(
                "Sidecar not ready (not found in connection store) for IDE trying to connect",
                extra=key.log_fields(),
            )
        return is_connected

    async def _send_error_to_client(
        self,
        conn_type: ConnectionType,
        conn_key: ConnectionKey,
        error_message: ws_pb2.WebsocketMessage,
    ) -> None:
        """Send an error message to a connected client, handling exceptions."""
        client_ws = self.registry.get_connection(conn_type, conn_key)
        if client_ws is not None:
            try:
                await send_websocket_message(client_ws, error_message)
            except Exception as e:
                log.error(
                    f"Failed to send error to client: {e}", extra=conn_key.log_fields()
                )

    def _store_connection(
        self, conn_type: ConnectionType, conn_key: ConnectionKey, websocket: WebSocket
    ) -> None:
        """Store a WebSocket connection both locally and in the connection store."""
        existing_worker = self.cx_store.get_worker_id(conn_type, conn_key)
        if existing_worker:
            log.warning(
                f"{conn_type} session already active (handled by worker {existing_worker}). Closing new connection.",
                extra=conn_key.log_fields(),
            )
            raise ConnectionError(
                f"{conn_type} session already active (handled by worker {existing_worker})"
            )

        self.cx_store.register_connection(conn_type, conn_key)
        self.registry.register_connection(conn_type, conn_key, websocket)
        log.info(
            f"{conn_type} connection registered by worker {settings.worker_id} in store and locally.",
            extra=conn_key.log_fields(),
        )

    def _remove_connection(
        self, conn_type: ConnectionType, conn_key: ConnectionKey
    ) -> None:
        """Remove a connection from both local storage and the connection store."""
        self.cx_store.deregister_connection(conn_type, conn_key)
        self.registry.deregister_connection(conn_type, conn_key)
        log.info(
            f"{conn_type} connection removed from local store and cx_store by worker {settings.worker_id}.",
            extra=conn_key.log_fields(),
        )

    async def _handle_connection(
        self,
        conn_type: ConnectionType,
        conn_key: ConnectionKey,
        websocket: WebSocket,
    ) -> None:
        """Generic connection handler for both sidecar and IDE connections."""
        log_extra = conn_key.log_fields()

        try:
            while True:
                message = await recv_websocket_message(websocket)
                message_type_enum = message.message_type
                handler = self._message_handlers.get(message_type_enum)

                if not handler:
                    message_type_name = (
                        message.WhichOneof("message") or "UNKNOWN_PAYLOAD"
                    )
                    # Get the enum name if possible for better logging
                    enum_name = ws_pb2.WebsocketMessage.MessageType.Name(
                        message_type_enum
                    )
                    log.error(
                        f"Received unexpected message type {enum_name} ({message_type_enum}) with payload '{message_type_name}' from {conn_type}",
                        extra=log_extra,
                    )
                    # Using a generic error for unexpected types
                    error_msg = MessageFactory.create_push_error(
                        f"Invalid or unsupported message type: {enum_name}"
                    )
                    # Send error to the client that sent the invalid message
                    await send_websocket_message(websocket, error_msg)
                    continue

                await handler(conn_key, message)

        except WebSocketDisconnect:
            log.info(
                f"{conn_type} disconnected",
                extra=log_extra,
            )
        except ValueError as ve:  # Catch invalid message format
            log.error(
                f"Invalid message from {conn_type}: {ve}",
                extra=log_extra,
            )
            # Attempt to send an error back if the websocket is still open
            try:
                error_msg = MessageFactory.create_push_error(
                    f"Invalid message format: {ve}"
                )
                await send_websocket_message(websocket, error_msg)
            except Exception as e_send:
                log.error(
                    f"Failed to send ValueError to client: {e_send}", extra=log_extra
                )
        except Exception:
            log.exception(
                f"Error in {conn_type} WS handler",
                extra=log_extra,
            )
            # Attempt to send a generic error back
            try:
                error_msg = MessageFactory.create_push_error(
                    "Internal server error during WebSocket handling."
                )
                await send_websocket_message(websocket, error_msg)
            except Exception as e_send_generic:
                log.error(
                    f"Failed to send generic error to client: {e_send_generic}",
                    extra=log_extra,
                )
        finally:
            log.info(f"Detaching {conn_type}", extra=log_extra)
            self._remove_connection(conn_type, conn_key)

    async def attach_sidecar(
        self,
        app_id: str,
        deployment_id: str,
        websocket: WebSocket,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Attach a sidecar WebSocket connection."""
        # Verify the deployment if provided with a verifier
        is_valid, error_message, log_extra = self.deployment_verifier.verify_deployment(
            app_id, deployment_id
        )

        if not is_valid:
            log.error(
                f"Failed to verify deployment {app_id}/{deployment_id}: {error_message}"
            )
            await websocket.close(code=1008, reason=error_message)
            return

        conn_key = self._make_key(app_id, deployment_id, org_id, user_id)
        log.info(
            "Attempting to attach SIDECAR",
            extra=log_extra,
        )

        try:
            conn_type = ConnectionType.SIDECAR
            self._store_connection(conn_type, conn_key, websocket)
            log.info("SIDECAR connection stored", extra=log_extra)
            await self._handle_connection(conn_type, conn_key, websocket)
        except ConnectionError as e:
            log.warning(
                f"Failed to store SIDECAR connection: {e}",
                extra=log_extra,
            )
            await websocket.close(code=1008, reason=str(e))
            return
        except Exception as e_outer:
            log.exception(
                f"Outer error attaching SIDECAR: {e_outer}",
                extra=log_extra,
            )
            await websocket.close(
                code=1011, reason="Unexpected server error during sidecar attach"
            )

    async def attach_ide(
        self,
        app_id: str,
        deployment_id: str,
        websocket: WebSocket,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Attach an IDE WebSocket connection."""
        # Verify the deployment if provided with a verifier
        is_valid, error_message, log_extra = self.deployment_verifier.verify_deployment(
            app_id, deployment_id
        )

        if not is_valid:
            log.error(
                f"Failed to verify deployment {app_id}/{deployment_id}: {error_message}"
            )
            await websocket.close(code=1008, reason=error_message)
            return

        conn_key = self._make_key(app_id, deployment_id, org_id, user_id)
        log.info(
            "Attempting to attach IDE",
            extra=log_extra,
        )

        if not self.is_sidecar_ready(app_id, deployment_id, org_id, user_id):
            reason = (
                f"Sidecar for app {app_id}, deployment {deployment_id} is not ready."
            )
            log.warning(
                f"IDE connection refused: {reason}",
                extra=log_extra,
            )
            await websocket.close(code=1008, reason="Sidecar not connected")
            return

        try:
            conn_type = ConnectionType.IDE
            self._store_connection(conn_type, conn_key, websocket)
            log.info("IDE connection stored", extra=log_extra)
            await self._handle_connection(conn_type, conn_key, websocket)
        except ConnectionError as e:
            log.warning(
                f"Failed to store IDE connection: {e}",
                extra=log_extra,
            )
            await websocket.close(code=1008, reason=str(e))
            return
        except Exception as e_outer:
            log.exception(
                f"Outer error attaching IDE: {e_outer}",
                extra=log_extra,
            )
            await websocket.close(
                code=1011, reason="Unexpected server error during IDE attach"
            )

    # --- Message Handlers ---

    async def _handle_push_request(
        self, key: ConnectionKey, request: ws_pb2.WebsocketMessage
    ) -> None:
        """Handle a push request from the IDE to the sidecar."""
        from code_sync_proxy.ws.interfaces import PushStatus

        push_request = request.push_message
        log.info(
            f"Received push request from IDE ({len(request.SerializeToString())} bytes)",
            extra=key.log_fields(),
        )

        # Create push operation record
        self.push_repo.create(
            id=push_request.push_id,
            deployment_id=key.deployment_id,
            status=PushStatus.PUSHING,
            code_diff=push_request.code_diff,
            change_description=push_request.change_description,
        )

        # Locate the sidecar connection
        target_worker_id = self.cx_store.get_worker_id(ConnectionType.SIDECAR, key)
        sidecar_ws = None

        if target_worker_id == settings.worker_id:
            sidecar_ws = self.registry.get_connection(ConnectionType.SIDECAR, key)

        # Handle errors if sidecar not found
        if target_worker_id != settings.worker_id:
            log.error(
                "Sidecar not connected during push request or on different worker",
                extra=key.log_fields(),
            )
            error_msg = MessageFactory.create_push_error(
                f"Sidecar on different worker ({target_worker_id}). Cross-worker messaging required."
            )
            await self._send_error_to_client(ConnectionType.IDE, key, error_msg)
            self.push_repo.update(push_request.push_id, status=PushStatus.FAILED)
            return

        # Handle case where sidecar is on this worker but not found in local storage
        if sidecar_ws is None:
            log.error("Sidecar not found in local store", extra=key.log_fields())
            error_msg = MessageFactory.create_push_error(
                "Sidecar connection lost or internal state error."
            )
            await self._send_error_to_client(ConnectionType.IDE, key, error_msg)
            self.push_repo.update(push_request.push_id, status=PushStatus.FAILED)
            return

        # Forward the push request to the sidecar
        try:
            ws_msg = ws_pb2.WebsocketMessage(
                message_type=ws_pb2.WebsocketMessage.MessageType.PUSH_REQUEST,
                push_message=push_request,
            )
            await send_websocket_message(sidecar_ws, ws_msg)
            log.info("Forwarded push data to sidecar", extra=key.log_fields())
            self.push_repo.update(push_request.push_id, status=PushStatus.PUSHED)
        except Exception as e:
            log.exception(
                f"Failed to send data to sidecar: {e}", extra=key.log_fields()
            )
            self.push_repo.update(push_request.push_id, status=PushStatus.FAILED)
            error_msg = MessageFactory.create_push_error("Failed to reach sidecar")
            await self._send_error_to_client(ConnectionType.IDE, key, error_msg)

    async def _handle_push_response(
        self, key: ConnectionKey, response: ws_pb2.WebsocketMessage
    ) -> None:
        """Handle a push response from the sidecar to the IDE."""
        push_response = response.push_response
        log.info(
            f"Received push response, status {push_response.status}",
            extra=key.log_fields(),
        )

        if push_response.status != PushStatusPb.COMPLETED:
            log.error(
                f"Sidecar push not completed: status: {push_response.status}, error: {push_response.error_message}",
                extra=key.log_fields(),
            )

        # Forward the response to the IDE
        target_ide_worker_id = self.cx_store.get_worker_id(ConnectionType.IDE, key)

        # Check if IDE is connected to this worker
        if not target_ide_worker_id:
            log.warning(
                "IDE not found in connection store when handling push response",
                extra=key.log_fields(),
            )
            return

        if target_ide_worker_id != settings.worker_id:
            log.warning(
                f"IDE on different worker ({target_ide_worker_id}) for push response",
                extra=key.log_fields(),
            )
            # TODO: Implement cross-worker message forwarding (Redis Pub/Sub)
            return

        # Get the IDE WebSocket
        ide_ws = self.registry.get_connection(ConnectionType.IDE, key)
        if ide_ws is None:
            log.warning(
                "IDE not found in local store for push response",
                extra=key.log_fields(),
            )
            return

        # Forward the response
        await send_websocket_message(ide_ws, response)
        log.info("Forwarded push response to IDE", extra=key.log_fields())

    async def _handle_verification_request(
        self, key: ConnectionKey, request_ws_message: ws_pb2.WebsocketMessage
    ) -> None:
        """Handle a verification request from the IDE."""
        log.info(
            "Received verification request",
            extra=key.log_fields(),
        )
        verification_request = request_ws_message.verification_request

        # Get the IDE WebSocket
        ide_ws = self.registry.get_connection(ConnectionType.IDE, key)
        if ide_ws is None:
            log.warning(
                "IDE no longer connected locally when handling verification request."
            )
            return

        # Prepare the test payload for the verification task
        tests_payload = json_format.MessageToDict(
            verification_request.tests,
            preserving_proto_field_name=True,
            use_integers_for_enums=False,
        )

        # Queue the verification task
        try:
            # Extract user_id from the connection key, might be None in standalone mode
            user_id = key.user_id if key.user_id != "standalone" else None

            # Run the verification using the pluggable runner
            await self.verification_runner.run_verification(
                user_id=user_id,
                app_id=key.app_id,
                deployment_id=key.deployment_id,
                push_id=verification_request.push_id,
                tests_payload=tests_payload,
            )

            log.info(
                f"Enqueued verification for push_id {verification_request.push_id}",
                extra={"push_id": verification_request.push_id, **key.log_fields()},
            )

            # Send "in progress" response
            await send_websocket_message(
                ide_ws,
                MessageFactory.create_verification_in_progress(),
            )
        except Exception as e:
            log.exception(
                f"Failed to enqueue verification task: {e}",
                extra={"push_id": verification_request.push_id, **key.log_fields()},
            )

            # Send error response
            await send_websocket_message(
                ide_ws,
                MessageFactory.create_verification_error(
                    "Internal server error during verification enqueue"
                ),
            )
