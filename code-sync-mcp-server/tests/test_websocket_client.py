import asyncio
import re
from unittest.mock import AsyncMock, patch, MagicMock, call

import pytest
import pytest_asyncio
import websockets

from code_sync_mcp.websocket_client import (
    WebsocketClient,
    PushFuture,
    PushResult,
    RequestRequiresReconnectError,
)
from code_sync_mcp.client_manager import ClientManager
from code_sync_mcp.pb import ws_pb2
from code_sync_mcp.push_handler import PushHandler

PushStatusPb = ws_pb2.PushResponse.PushStatus


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables required by RsyncClient."""
    monkeypatch.setenv("BIFROST_API_KEY", "test-api-key")
    monkeypatch.setenv("BIFROST_API_URL", "http://test-server:8000")
    monkeypatch.setenv("BIFROST_WS_API_URL", "ws://test-server:8000")


@pytest_asyncio.fixture(autouse=True)
async def cleanup_client_manager():
    """Ensure ClientManager is empty before and after each test."""
    ClientManager._active_clients.clear()
    yield
    await ClientManager.close_all()  # Ensure any created clients are closed
    ClientManager._active_clients.clear()


# --- ClientManager Tests ---


@pytest.mark.asyncio
async def test_client_manager_create_new():
    """Test creating and retrieving a client using ClientManager."""
    app_root = "/path/to/app"
    app_id = "test_app_id"
    deployment_id = "test_deployment_id"
    key = (app_id, deployment_id)

    with patch.object(
        WebsocketClient, "connect", new_callable=AsyncMock
    ) as mock_connect:
        mock_connect.side_effect = lambda: ClientManager._active_clients[
            (app_id, deployment_id)
        ]._connected.set()

        client = await ClientManager.create(app_id, app_root, deployment_id)
        assert isinstance(client, WebsocketClient)
        assert client.app_id == app_id
        assert client.deployment_id == deployment_id
        assert (
            await ClientManager.get_or_create(app_id, app_root, deployment_id) is client
        )
        assert key in ClientManager._active_clients
        mock_connect.assert_awaited_once()

    await ClientManager.close_all()
    ClientManager._active_clients.clear()


@pytest.mark.asyncio
async def test_client_manager_create_duplicate():
    """Test that creating a client with an existing (app_id, deployment_id) pair raises ValueError."""
    app_name = "test_app"
    deployment_id = "test_deployment_id"
    key = (app_name, deployment_id)

    # Mock connect for the first creation
    with patch.object(
        WebsocketClient, "connect", new_callable=AsyncMock
    ) as mock_connect:
        mock_connect.side_effect = lambda: ClientManager._active_clients[
            key
        ]._connected.set()

        await ClientManager.create(app_name, "/root1", deployment_id)

        with pytest.raises(
            ValueError, match=re.escape(f"Client for {key} already exists")
        ):
            await ClientManager.create(app_name, "/root2", deployment_id)  # Duplicate

    await ClientManager.close_all()
    ClientManager._active_clients.clear()


# --- WebsocketClient Tests ---


@pytest_asyncio.fixture
async def test_client():
    """Fixture to create an WebsocketClient instance for testing."""
    # Use ClientManager to create so connect is auto-called, but mock connect logic
    app_id = "client_test_app"
    app_root = "/test/app/root"
    deployment_id = "client_test_deployment_id"

    # Mock RsyncHandler for all tests using this client fixture
    with patch("code_sync_mcp.websocket_client.PushHandler") as MockPushHandler, patch(
        "code_sync_mcp.websocket_client.websockets.connect"
    ) as mock_ws_connect:

        mock_push_handler_instance = PushHandler()
        # Default behavior for generate_batch, can be overridden in specific tests
        mock_push_handler_instance.generate_batch = AsyncMock(
            return_value=b"mock_batch_data"
        )
        MockPushHandler.return_value = mock_push_handler_instance

        # Configure the mock websocket connection
        mock_websocket = AsyncMock()
        mock_websocket.closed = False
        mock_ws_connect.return_value.__aenter__.return_value = mock_websocket

        client = WebsocketClient(app_id, app_root, deployment_id)
        client.check_readiness = AsyncMock(return_value=True)

        try:
            await client.connect()
        except asyncio.TimeoutError:
            pytest.fail("Client did not connect within timeout")
        except Exception as e:
            pytest.fail(f"Client connection failed: {e}")

        yield client  # Provide the connected client to the test

        # Cleanup: Ensure client is closed and task is handled
        await client.close()

        # Access the task directly for cleanup check
        if client._connect_task and not client._connect_task.done():
            try:
                await asyncio.wait_for(
                    client._connect_task, timeout=0.1
                )  # Allow task to finish if closing didn't cancel fully
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass  # Expected if close cancelled it properly
            except Exception as e:
                print(f"Warning: Exception during connect_task cleanup: {e}")

        if client._request_waiter and not client._request_waiter.done():
            client._request_waiter.cancel()
        if client._close_waiter and not client._close_waiter.done():
            client._close_waiter.cancel()

        # Ensure ClientManager is cleared if fixture somehow bypassed autouse fixture
        ClientManager._active_clients.clear()


def _cleanup_client(client: WebsocketClient):
    if client._connect_task and not client._connect_task.done():
        client._connect_task.cancel()
    if client._request_waiter and not client._request_waiter.done():
        client._request_waiter.cancel()
    if client._close_waiter and not client._close_waiter.done():
        client._close_waiter.cancel()


@pytest.mark.asyncio
async def test_client_connect_success(test_client: WebsocketClient):
    """Test successful client connection via the fixture."""
    assert test_client._connected.is_set()
    assert test_client._websocket is not None
    assert not test_client._websocket.closed
    assert test_client._connect_task is not None
    assert not test_client._connect_task.done()


@pytest.mark.asyncio
@patch("code_sync_mcp.websocket_client.websockets.connect")
async def test_client_connect_retry(mock_connect):
    """Test that the client retries connection after failure."""
    app_id = "test_app_id"
    app_root = "/retry/root"
    deployment_id = "test_deployment_id"

    mock_websocket = AsyncMock()
    mock_websocket.closed = False

    mock_connect.side_effect = [
        ConnectionRefusedError("Test connection refused"),
        # Second call returns something that works as an async context manager
        MagicMock(
            __aenter__=AsyncMock(return_value=mock_websocket),
            __aexit__=AsyncMock(return_value=False),
        ),
    ]

    client = WebsocketClient(app_id, deployment_id, app_root)
    client.check_readiness = AsyncMock(return_value=True)
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        client._connect_task = asyncio.create_task(client.connect())

        try:
            # Allow time for one retry cycle
            await asyncio.wait_for(client._connected.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pytest.fail("Client did not connect after simulated retry")

        assert client._connected.is_set()
        # Check it was called twice
        mock_connect.assert_has_calls(
            [
                call(
                    "ws://test-server:8000/api/v1/push/ide/test_app_id/test_deployment_id",
                    additional_headers={"X-API-Key": "test-api-key"},
                ),
                call(
                    "ws://test-server:8000/api/v1/push/ide/test_app_id/test_deployment_id",
                    additional_headers={"X-API-Key": "test-api-key"},
                ),
            ]
        )
        # Check it slept between retries
        mock_sleep.assert_awaited_once_with(5)

        await client.close()

    _cleanup_client(client)


@pytest.mark.asyncio
@patch("code_sync_mcp.websocket_client.websockets.connect")
async def test_client_connect_readiness_failure(mock_ws_connect):
    """Test that the client fails to connect if readiness checks consistently fail."""
    app_id = "readiness_fail_app"
    app_root = "/readiness/fail/root"
    deployment_id = "readiness_fail_deployment_id"

    client = WebsocketClient(app_id, deployment_id, app_root)
    # Mock check_readiness to always return False
    client.check_readiness = AsyncMock(return_value=False)
    client._connection_timeout = 0.01

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RuntimeError) as excinfo:
            # Wait for the connect task to complete (it should fail)
            await client.connect()

        assert "Deployment not available after 3 readiness checks" in str(excinfo.value)
        assert client.check_readiness.call_count == 3
        assert mock_sleep.call_count == 3

        # Ensure websocket connect was not called
        mock_ws_connect.assert_not_called()

    _cleanup_client(client)


@pytest.mark.asyncio
async def test_client_close(test_client: WebsocketClient):
    """Test closing the client connection."""
    assert test_client._connected.is_set()
    connect_task = test_client._connect_task
    websocket_mock = test_client._websocket

    await test_client.close()

    assert test_client._close_requested.is_set()
    assert test_client._websocket is None
    websocket_mock.close.assert_awaited_once()

    assert connect_task.cancelled() or connect_task.done()
    # Give event loop a chance to process cancellation
    await asyncio.sleep(0)
    assert connect_task.done()

    _cleanup_client(test_client)


# Placeholder for sync tests - requires more mocking (subprocess, temp files)
@pytest.mark.asyncio
async def test_client_push_triggers_send(test_client: WebsocketClient):
    """Test that calling push triggers the send_push_request eventually."""
    mock_send_sync = AsyncMock(side_effect=lambda ws, fut: fut.set_result(True))

    with patch.object(test_client, "_dispatch_request_handler", mock_send_sync):
        # Trigger sync
        push_task = asyncio.create_task(
            test_client.push("test-push-request", "test-diff")
        )

        # Allow connect loop to process the sync request
        await asyncio.sleep(0.1)  # Give time for _sync_needed event processing

        # Check that send_sync_batch was called
        mock_send_sync.assert_awaited_once()

        # Ensure the sync task completed successfully
        await push_task


@pytest.mark.asyncio
async def test_client_push_locking(test_client: WebsocketClient):
    """Test that only one push operation runs at a time."""
    push_finish_event = asyncio.Event()
    push_start_event = asyncio.Event()

    async def slow_send_push_request(websocket, push_future):
        push_start_event.set()  # Signal that push has started
        await push_finish_event.wait()  # Wait until signalled to finish
        if not push_future.cancelled():
            push_future.set_result(True)

    with patch.object(
        test_client, "_dispatch_request_handler", side_effect=slow_send_push_request
    ):
        # Start the first sync, it should block inside send_sync_batch
        push_task1 = asyncio.create_task(
            test_client.push("test-push-request", "test-diff")
        )
        await asyncio.wait_for(push_start_event.wait(), timeout=1.0)

        # Try to start a second sync - it should block on the lock in sync() itself
        push_task2 = asyncio.create_task(
            test_client.push("test-push-request", "test-diff")
        )

        # Give it a moment to see if it starts (it shouldn't)
        await asyncio.sleep(0.05)
        assert (
            test_client._dispatch_request_handler.call_count == 1
        )  # Only first sync should have called send_sync_batch

        # Allow the first sync to finish
        push_finish_event.set()
        await asyncio.wait_for(push_task1, timeout=1.0)

        # Now the second sync should acquire the lock and run
        push_start_event.clear()  # Reset for the second sync
        push_finish_event.clear()  # Reset for the second sync
        await asyncio.wait_for(push_start_event.wait(), timeout=1.0)
        assert test_client._dispatch_request_handler.call_count == 2

        # Allow the second sync to finish
        push_finish_event.set()
        await asyncio.wait_for(push_task2, timeout=1.0)


@pytest.mark.asyncio
async def test_client_push_when_closed(test_client: WebsocketClient):
    """Test that push raises an error if called after close."""
    await test_client.close()
    with pytest.raises(
        RuntimeError, match="Cannot push, WebsocketClient is closing or closed"
    ):
        await test_client.push("test-push-request", "test-diff")


@pytest.mark.asyncio
async def test_client_push_when_disconnected_initially():
    """Test that push raises an error if connection task isn't running."""
    client = WebsocketClient("no_connect_app", "/root", "deployment")
    # Don't start the connect task
    with pytest.raises(
        RuntimeError,
        match="Cannot push, WebsocketClient connection task is not running",
    ):
        await client.push("test-push-request", "test-diff")


# --- _dispatch_request_handler Tests ---


@pytest.mark.asyncio
async def test_send_push_request_success(
    test_client: WebsocketClient,
):
    """Test successful push request sending, assuming RsyncHandler provides data."""
    mock_ws = test_client._websocket
    mock_push_handler = test_client._push_handler
    mock_push_handler.generate_batch.return_value = (
        b"fake_batch_data_from_mock_generator"
    )

    ws_message = ws_pb2.WebsocketMessage(
        message_type=ws_pb2.WebsocketMessage.MessageType.PUSH_RESPONSE,
        push_response=ws_pb2.PushResponse(
            status=PushStatusPb.COMPLETED,
        ),
    )
    mock_ws.recv.return_value = ws_message.SerializeToString()

    push_future = PushFuture("test-code-diff", "test-change-description")

    await test_client._dispatch_request_handler(mock_ws, push_future)

    # Verify RsyncBatchGenerator was called
    mock_push_handler.generate_batch.assert_awaited_once_with(test_client.app_root)

    mock_ws.send.assert_awaited_once_with(
        ws_pb2.WebsocketMessage(
            message_type=ws_pb2.WebsocketMessage.MessageType.PUSH_REQUEST,
            push_message=ws_pb2.PushMessage(
                push_id=push_future.push_id,
                batch_file=b"fake_batch_data_from_mock_generator",
                code_diff="test-code-diff",
                change_description="test-change-description",
            ),
        ).SerializeToString()
    )
    # Verify ACK received
    mock_ws.recv.assert_awaited_once()

    # Verify future completed successfully
    assert push_future.done()
    assert push_future.result() == PushResult(push_future.push_id, "done")


@pytest.mark.asyncio
async def test_send_push_request_no_changes(
    test_client: WebsocketClient,
):
    """Test push request when RsyncHandler indicates no changes (empty batch data)."""
    mock_ws = test_client._websocket
    mock_push_handler = test_client._push_handler

    # Simulate RsyncHandler returning empty bytes (no changes)
    mock_push_handler.generate_batch.return_value = b""

    push_future = PushFuture("test-code-diff", "test-change-description")
    await test_client._dispatch_request_handler(mock_ws, push_future)

    # Verify RsyncHandler was called
    mock_push_handler.generate_batch.assert_awaited_once_with(test_client.app_root)

    # Verify data was NOT sent
    mock_ws.send.assert_not_awaited()

    # Verify future completed successfully (no changes is success)
    assert push_future.done()
    assert push_future.result() == PushResult(push_future.push_id, "no-changes")


@pytest.mark.asyncio
async def test_send_push_request_rsync_failure(
    test_client: WebsocketClient,
):
    """Test push request when the RsyncHandler fails."""
    mock_ws = test_client._websocket
    mock_push_handler = test_client._push_handler

    # Simulate RsyncHandler raising an error
    mock_push_handler.generate_batch.side_effect = RuntimeError(
        "Mock rsync process failed"
    )

    push_future = PushFuture("test-code-diff", "test-change-description")

    # Verify hitting the exception from the generator
    with pytest.raises(RuntimeError, match="Mock rsync process failed"):
        await test_client._dispatch_request_handler(mock_ws, push_future)

    # Verify RsyncHandler was called
    mock_push_handler.generate_batch.assert_awaited_once_with(test_client.app_root)

    # Verify nothing was sent
    mock_ws.send.assert_not_awaited()
    assert push_future.exception() is not None


@pytest.mark.asyncio
async def test_send_push_request_recv_timeout(
    test_client: WebsocketClient,
):
    """Test push request when waiting for ACK times out."""
    mock_ws = test_client._websocket
    mock_push_handler = test_client._push_handler
    # Assume rsync generator succeeds for this test
    mock_push_handler.generate_batch.return_value = b"some_batch_data"

    mock_ws.recv = AsyncMock(side_effect=asyncio.TimeoutError)  # Simulate timeout

    push_future = PushFuture("test-code-diff", "test-change-description")

    # Verify hitting the exception
    with pytest.raises(RequestRequiresReconnectError):
        await test_client._dispatch_request_handler(mock_ws, push_future)

    # Verify RsyncHandler, send occurred
    mock_push_handler.generate_batch.assert_awaited_once_with(test_client.app_root)
    mock_ws.send.assert_awaited_once_with(
        ws_pb2.WebsocketMessage(
            message_type=ws_pb2.WebsocketMessage.MessageType.PUSH_REQUEST,
            push_message=ws_pb2.PushMessage(
                push_id=push_future.push_id,
                batch_file=b"some_batch_data",
                code_diff="test-code-diff",
                change_description="test-change-description",
            ),
        ).SerializeToString()
    )
    mock_ws.recv.assert_awaited_once()  # Attempted recv


@pytest.mark.asyncio
async def test_send_push_request_recv_connection_closed(
    test_client: WebsocketClient,
):
    """Test push request when connection closes while waiting for ACK."""
    mock_ws = test_client._websocket
    mock_push_handler = test_client._push_handler
    # Assume rsync generator succeeds
    mock_push_handler.generate_batch.return_value = b"some_batch_data"

    close_exception = websockets.exceptions.ConnectionClosedOK(rcvd=None, sent=None)
    mock_ws.recv = AsyncMock(side_effect=close_exception)  # Simulate connection closed

    push_future = PushFuture("test-code-diff", "test-change-description")

    # Verify hitting the exception
    with pytest.raises(RequestRequiresReconnectError):
        await test_client._dispatch_request_handler(mock_ws, push_future)

    assert push_future.exception() is not None

    # Verify RsyncHandler, send occurred
    mock_push_handler.generate_batch.assert_awaited_once_with(test_client.app_root)
    mock_ws.send.assert_awaited_once_with(
        ws_pb2.WebsocketMessage(
            message_type=ws_pb2.WebsocketMessage.MessageType.PUSH_REQUEST,
            push_message=ws_pb2.PushMessage(
                push_id=push_future.push_id,
                batch_file=b"some_batch_data",
                code_diff="test-code-diff",
                change_description="test-change-description",
            ),
        ).SerializeToString()
    )
