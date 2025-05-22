import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from code_sync_proxy.pb import ws_pb2
from code_sync_proxy.ws.manager import WebSocketManager
from code_sync_proxy.ws.interfaces import PushStatus
from code_sync_proxy.ws.standalone import InMemoryPushRepository

import logging

log = logging.getLogger(__name__)


# Message type enums for cleaner reference
PushStatusPb = ws_pb2.PushResponse.PushStatus


def make_connection_manager() -> WebSocketManager:
    """Create a WebSocketManager for testing."""
    manager = WebSocketManager()
    # Replace the push_repo with a mock to facilitate assertions
    manager.push_repo = MagicMock(spec=InMemoryPushRepository)
    return manager


@pytest.mark.asyncio
async def test_handle_push_request_via_websocket():
    """Test handling a push request from IDE to sidecar."""
    connection_manager = make_connection_manager()
    app_id = "test-app-id"
    deployment_id = "test-deployment-id"
    push_id_val = str(uuid4())

    # Configure IDE to send request and then wait + disconnect.
    # This gives time for the sidecar to receive the request, process it, and send a response.
    async def ide_ws_receive_gen():
        async def wait_and_disconnect():
            await asyncio.sleep(0.5)
            raise WebSocketDisconnect(code=1000, reason="Test finished")

        msg = ws_pb2.WebsocketMessage(
            message_type=ws_pb2.WebsocketMessage.MessageType.PUSH_REQUEST,
            push_message=ws_pb2.PushMessage(
                push_id=push_id_val,
                batch_file=b"test batch file",
                code_diff="test diff",
                change_description="test description",
            ),
        ).SerializeToString()
        mock_ide_ws.receive_bytes = wait_and_disconnect
        return msg

    mock_ide_ws = AsyncMock(spec=WebSocket)
    mock_ide_ws.receive_bytes = ide_ws_receive_gen
    mock_ide_ws.send_bytes = AsyncMock()

    # Attach the sidecar to receive the push request and return a response message.
    push_response_payload = ws_pb2.PushResponse(
        status=PushStatusPb.COMPLETED,
    )
    sidecar_message = ws_pb2.WebsocketMessage(
        message_type=ws_pb2.WebsocketMessage.MessageType.PUSH_RESPONSE,
        push_response=push_response_payload,
    )

    async def sidecar_receive_gen() -> bytes:
        await asyncio.sleep(0.5)
        return sidecar_message.SerializeToString()

    mock_sidecar_ws = AsyncMock(spec=WebSocket)
    mock_sidecar_ws.receive_bytes = sidecar_receive_gen
    mock_sidecar_ws.send_bytes = AsyncMock()
    asyncio.create_task(
        connection_manager.attach_sidecar(app_id, deployment_id, mock_sidecar_ws)
    )

    # Attach the IDE to push the code change.
    mock_push_op = {"id": push_id_val}
    connection_manager.push_repo.create.return_value = mock_push_op
    ide_task = asyncio.create_task(
        connection_manager.attach_ide(app_id, deployment_id, mock_ide_ws)
    )

    try:
        await asyncio.wait_for(ide_task, timeout=2.0)
    except asyncio.TimeoutError:
        ide_task.cancel()
        await asyncio.gather(ide_task, return_exceptions=True)
        pytest.fail("IDE task timed out during message processing")
    except WebSocketDisconnect:
        pass

    # Assert we create the push data.
    connection_manager.push_repo.create.assert_called_once_with(
        id=push_id_val,
        deployment_id=deployment_id,
        status=PushStatus.PUSHING,
        code_diff="test diff",
        change_description="test description",
    )

    # Assert message was forwarded from IDE to Sidecar
    mock_sidecar_ws.send_bytes.assert_awaited_once()
    sent_to_sidecar_bytes = mock_sidecar_ws.send_bytes.call_args[0][0]
    sent_to_sidecar_message = ws_pb2.WebsocketMessage()
    sent_to_sidecar_message.ParseFromString(sent_to_sidecar_bytes)

    assert (
        sent_to_sidecar_message.message_type
        == ws_pb2.WebsocketMessage.MessageType.PUSH_REQUEST
    )
    assert sent_to_sidecar_message.push_message.push_id == push_id_val
    assert sent_to_sidecar_message.push_message.batch_file == b"test batch file"
    assert sent_to_sidecar_message.push_message.code_diff == "test diff"
    assert sent_to_sidecar_message.push_message.change_description == "test description"

    connection_manager.push_repo.update.assert_called_with(
        push_id_val, status=PushStatus.PUSHED
    )

    # Assert message was forwarded from Sidecar to IDE
    mock_ide_ws.send_bytes.assert_called_once()
    sent_to_ide_bytes = mock_ide_ws.send_bytes.call_args[0][0]
    sent_to_ide_message = ws_pb2.WebsocketMessage()
    sent_to_ide_message.ParseFromString(sent_to_ide_bytes)

    assert (
        sent_to_ide_message.message_type
        == ws_pb2.WebsocketMessage.MessageType.PUSH_RESPONSE
    )
    assert sent_to_ide_message.push_response == push_response_payload
