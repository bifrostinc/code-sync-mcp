import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
import websockets

from code_sync_mcp.verify_handler import (
    VerifyHandler,
    VerifyFuture,
    VerifyRequest,
    HTTPTest,
    BrowserTest,
)
from code_sync_mcp.pb import ws_pb2

VerificationStatusPb = ws_pb2.VerificationResponse.VerificationStatus


# Fixtures
@pytest_asyncio.fixture
async def verify_handler():
    """Provides an instance of VerifyHandler."""
    return VerifyHandler()


@pytest_asyncio.fixture
def mock_websocket():
    """Provides a mock websockets.ClientConnection."""
    mock_ws = AsyncMock(spec=websockets.ClientConnection)
    mock_ws.send = AsyncMock()
    return mock_ws


@pytest.fixture
def sample_http_test_data():
    """Provides sample HTTPTest data."""
    return HTTPTest(
        path="/test", method="GET", headers={"X-Test": "true"}, body="test_body"
    )


@pytest.fixture
def sample_browser_test_data():
    """Provides sample BrowserTest data."""
    return BrowserTest(workflow_steps=["click button", "verify text"])


@pytest.fixture
def sample_verify_request(sample_http_test_data, sample_browser_test_data):
    """Provides a sample VerifyRequest with both HTTP and Browser tests."""
    return VerifyRequest(tests=[sample_http_test_data, sample_browser_test_data])


@pytest.fixture
def sample_verify_request_http_only(sample_http_test_data):
    """Provides a sample VerifyRequest with only HTTP tests."""
    return VerifyRequest(tests=[sample_http_test_data])


@pytest.fixture
def sample_verify_request_browser_only(sample_browser_test_data):
    """Provides a sample VerifyRequest with only Browser tests."""
    return VerifyRequest(tests=[sample_browser_test_data])


@pytest.fixture
def sample_verify_request_empty():
    """Provides a sample VerifyRequest with no tests."""
    return VerifyRequest(tests=[])


@pytest.fixture
def sample_verify_future(sample_verify_request):
    """Provides a sample VerifyFuture."""
    return VerifyFuture(sync_id="test_sync_id", verify_request=sample_verify_request)


# Test Cases for _convert_verify_request_to_proto
def test_convert_http_test_to_proto(
    verify_handler: VerifyHandler, sample_http_test_data: HTTPTest
):
    """Tests conversion of a single HTTPTest to its protobuf equivalent."""
    sync_id = "test_sync_id_http"
    verify_req_data = VerifyRequest(tests=[sample_http_test_data])

    proto_verification_req = verify_handler._convert_verify_request_to_proto(
        sync_id, verify_req_data
    )

    assert proto_verification_req.push_id == sync_id
    assert len(proto_verification_req.tests.http_tests) == 1
    assert len(proto_verification_req.tests.browser_tests) == 0

    proto_http_test = proto_verification_req.tests.http_tests[0]
    assert proto_http_test.path == sample_http_test_data.path
    assert proto_http_test.method == ws_pb2.HttpTest.HttpMethod.GET  # Direct enum check
    assert proto_http_test.headers["X-Test"] == "true"
    assert proto_http_test.body == sample_http_test_data.body


def test_convert_browser_test_to_proto(
    verify_handler: VerifyHandler, sample_browser_test_data: BrowserTest
):
    """Tests conversion of a single BrowserTest to its protobuf equivalent."""
    sync_id = "test_sync_id_browser"
    verify_req_data = VerifyRequest(tests=[sample_browser_test_data])

    proto_verification_req = verify_handler._convert_verify_request_to_proto(
        sync_id, verify_req_data
    )

    assert proto_verification_req.push_id == sync_id
    assert len(proto_verification_req.tests.http_tests) == 0
    assert len(proto_verification_req.tests.browser_tests) == 1

    proto_browser_test = proto_verification_req.tests.browser_tests[0]
    assert (
        list(proto_browser_test.workflow_steps)
        == sample_browser_test_data.workflow_steps
    )


def test_convert_mixed_tests_to_proto(
    verify_handler: VerifyHandler,
    sample_verify_request: VerifyRequest,
    sample_http_test_data: HTTPTest,
    sample_browser_test_data: BrowserTest,
):
    """Tests conversion of mixed HTTPTest and BrowserTest to protobuf."""
    sync_id = "test_sync_id_mixed"

    proto_verification_req = verify_handler._convert_verify_request_to_proto(
        sync_id, sample_verify_request
    )

    assert proto_verification_req.push_id == sync_id
    assert len(proto_verification_req.tests.http_tests) == 1
    assert len(proto_verification_req.tests.browser_tests) == 1

    # HTTP Test verification
    proto_http_test = proto_verification_req.tests.http_tests[0]
    assert proto_http_test.path == sample_http_test_data.path
    assert proto_http_test.method == ws_pb2.HttpTest.HttpMethod.GET
    assert proto_http_test.headers["X-Test"] == "true"
    assert proto_http_test.body == sample_http_test_data.body

    # Browser Test verification
    proto_browser_test = proto_verification_req.tests.browser_tests[0]
    assert (
        list(proto_browser_test.workflow_steps)
        == sample_browser_test_data.workflow_steps
    )


def test_convert_empty_tests_to_proto(
    verify_handler: VerifyHandler, sample_verify_request_empty: VerifyRequest
):
    """Tests conversion with no tests."""
    sync_id = "test_sync_id_empty"

    proto_verification_req = verify_handler._convert_verify_request_to_proto(
        sync_id, sample_verify_request_empty
    )

    assert proto_verification_req.push_id == sync_id
    assert len(proto_verification_req.tests.http_tests) == 0
    assert len(proto_verification_req.tests.browser_tests) == 0


# Test Cases for handle_verify_request
@pytest.mark.asyncio
async def test_handle_verify_request_sends_correct_message(
    verify_handler: VerifyHandler,
    mock_websocket: AsyncMock,
    sample_verify_future: VerifyFuture,
    sample_http_test_data: HTTPTest,
    sample_browser_test_data: BrowserTest,
):
    """Tests that handle_verify_request sends a correctly formed WebsocketMessage."""

    message = ws_pb2.WebsocketMessage(
        message_type=ws_pb2.WebsocketMessage.MessageType.VERIFICATION_RESPONSE,
        verification_response=ws_pb2.VerificationResponse(
            status=VerificationStatusPb.IN_PROGRESS,
        ),
    )
    mock_websocket.recv.return_value = message.SerializeToString()

    await verify_handler.handle_verify_request(mock_websocket, sample_verify_future)

    mock_websocket.send.assert_awaited_once()
    sent_data = mock_websocket.send.call_args[0][0]

    ws_msg = ws_pb2.WebsocketMessage()
    ws_msg.ParseFromString(sent_data)

    assert (
        ws_msg.message_type == ws_pb2.WebsocketMessage.MessageType.VERIFICATION_REQUEST
    )

    verification_req = ws_msg.verification_request
    assert verification_req.push_id == sample_verify_future.sync_id
    assert len(verification_req.tests.http_tests) == 1
    assert len(verification_req.tests.browser_tests) == 1

    # Verify HTTP test details (as in conversion tests)
    proto_http_test = verification_req.tests.http_tests[0]
    assert proto_http_test.path == sample_http_test_data.path
    assert proto_http_test.method == ws_pb2.HttpTest.HttpMethod.GET
    assert proto_http_test.headers["X-Test"] == "true"
    assert proto_http_test.body == sample_http_test_data.body

    # Verify Browser test details (as in conversion tests)
    proto_browser_test = verification_req.tests.browser_tests[0]
    assert (
        list(proto_browser_test.workflow_steps)
        == sample_browser_test_data.workflow_steps
    )


@pytest.mark.asyncio
async def test_handle_verify_request_future_already_done(
    verify_handler: VerifyHandler,
    mock_websocket: AsyncMock,
    sample_verify_future: VerifyFuture,
):
    """Tests that handle_verify_request returns early if the future is already done."""
    sample_verify_future.set_result(None)  # Mark future as done

    # Patch log.warning to check if it's called
    with patch("code_sync_mcp.verify_handler.log.warning") as mock_log_warning:
        await verify_handler.handle_verify_request(mock_websocket, sample_verify_future)

    mock_websocket.send.assert_not_awaited()
    mock_log_warning.assert_called_once()
    assert (
        f"Verify future {id(sample_verify_future)} was already done"
        in mock_log_warning.call_args[0][0]
    )


@pytest.mark.parametrize(
    "http_method_str, proto_enum_val",
    [
        ("GET", ws_pb2.HttpTest.GET),
        ("POST", ws_pb2.HttpTest.POST),
        ("PUT", ws_pb2.HttpTest.PUT),
        ("DELETE", ws_pb2.HttpTest.DELETE),
    ],
)
def test_http_method_conversion(
    verify_handler: VerifyHandler, http_method_str: str, proto_enum_val
):
    """Tests the mapping of HTTP method strings to protobuf enums."""
    http_test_data = HTTPTest(path="/test", method=http_method_str)
    verify_req_data = VerifyRequest(tests=[http_test_data])
    sync_id = f"test_sync_id_{http_method_str.lower()}"

    proto_verification_req = verify_handler._convert_verify_request_to_proto(
        sync_id, verify_req_data
    )

    assert len(proto_verification_req.tests.http_tests) == 1
    proto_http_test = proto_verification_req.tests.http_tests[0]
    assert proto_http_test.method == proto_enum_val


def test_http_test_optional_fields(verify_handler: VerifyHandler):
    """Tests HTTPTest conversion with and without optional fields (headers, body)."""
    sync_id = "test_optional_fields"

    # Test 1: No optional fields
    http_test_no_optionals = HTTPTest(path="/no_opt", method="GET")
    verify_req_no_opt = VerifyRequest(tests=[http_test_no_optionals])
    proto_req_no_opt = verify_handler._convert_verify_request_to_proto(
        sync_id, verify_req_no_opt
    )

    assert len(proto_req_no_opt.tests.http_tests) == 1
    proto_http_no_opt = proto_req_no_opt.tests.http_tests[0]
    assert proto_http_no_opt.path == "/no_opt"
    assert proto_http_no_opt.method == ws_pb2.HttpTest.GET
    assert len(proto_http_no_opt.headers) == 0  # Should be empty map
    assert not proto_http_no_opt.HasField("body")  # Check optional field presence

    # Test 2: With headers, no body
    http_test_headers_only = HTTPTest(
        path="/headers_only", method="POST", headers={"Auth": "Basic"}
    )
    verify_req_headers_only = VerifyRequest(tests=[http_test_headers_only])
    proto_req_headers_only = verify_handler._convert_verify_request_to_proto(
        sync_id, verify_req_headers_only
    )

    assert len(proto_req_headers_only.tests.http_tests) == 1
    proto_http_headers_only = proto_req_headers_only.tests.http_tests[0]
    assert proto_http_headers_only.path == "/headers_only"
    assert proto_http_headers_only.method == ws_pb2.HttpTest.POST
    assert proto_http_headers_only.headers["Auth"] == "Basic"
    assert not proto_http_headers_only.HasField("body")

    # Test 3: With body, no headers
    http_test_body_only = HTTPTest(
        path="/body_only", method="PUT", body="request_payload"
    )
    verify_req_body_only = VerifyRequest(tests=[http_test_body_only])
    proto_req_body_only = verify_handler._convert_verify_request_to_proto(
        sync_id, verify_req_body_only
    )

    assert len(proto_req_body_only.tests.http_tests) == 1
    proto_http_body_only = proto_req_body_only.tests.http_tests[0]
    assert proto_http_body_only.path == "/body_only"
    assert proto_http_body_only.method == ws_pb2.HttpTest.PUT
    assert len(proto_http_body_only.headers) == 0
    assert proto_http_body_only.body == "request_payload"
    assert proto_http_body_only.HasField("body")

    # Test 4: With both headers and body (already covered by sample_http_test_data, but good to be explicit)
    http_test_all_optionals = HTTPTest(
        path="/all_opt",
        method="DELETE",
        headers={"Content-Type": "application/json"},
        body='{"id": 1}',
    )
    verify_req_all_opt = VerifyRequest(tests=[http_test_all_optionals])
    proto_req_all_opt = verify_handler._convert_verify_request_to_proto(
        sync_id, verify_req_all_opt
    )

    assert len(proto_req_all_opt.tests.http_tests) == 1
    proto_http_all_opt = proto_req_all_opt.tests.http_tests[0]
    assert proto_http_all_opt.path == "/all_opt"
    assert proto_http_all_opt.method == ws_pb2.HttpTest.DELETE
    assert proto_http_all_opt.headers["Content-Type"] == "application/json"
    assert proto_http_all_opt.body == '{"id": 1}'
    assert proto_http_all_opt.HasField("body")
