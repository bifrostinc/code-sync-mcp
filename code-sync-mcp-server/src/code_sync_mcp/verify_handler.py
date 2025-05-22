import logging
import websockets
from dataclasses import dataclass
from asyncio import Future
from typing import Literal
import asyncio

from code_sync_mcp.pb import ws_pb2

log = logging.getLogger(__name__)


@dataclass
class VerifyResult:
    sync_id: str
    status: str


RESPONSE_TIMEOUT = 10.0

VerificationStatusPb = ws_pb2.VerificationResponse.VerificationStatus


@dataclass
class HTTPTest:
    path: str
    method: Literal["GET", "POST", "PUT", "DELETE"]
    headers: dict[str, str] | None = None
    body: str | None = None


@dataclass
class BrowserTest:
    workflow_steps: list[str]


@dataclass
class VerifyRequest:
    tests: list[HTTPTest | BrowserTest]


class VerifyFuture(Future):
    def __init__(self, sync_id: str, verify_request: VerifyRequest):
        super().__init__()
        self.sync_id = sync_id
        self.verify_request = verify_request


class VerifyHandler:
    """
    Handles the verification of a sync.
    """

    def _convert_verify_request_to_proto(
        self, sync_id: str, verify_request_data: VerifyRequest
    ) -> ws_pb2.VerificationRequest:
        """
        Converts the VerifyRequest dataclass to a ws_pb2.VerificationRequest protobuf message.
        """
        proto_http_tests = []
        proto_browser_tests = []

        http_method_map = {
            "GET": ws_pb2.HttpTest.GET,
            "POST": ws_pb2.HttpTest.POST,
            "PUT": ws_pb2.HttpTest.PUT,
            "DELETE": ws_pb2.HttpTest.DELETE,
        }

        for test_item in verify_request_data.tests:
            if isinstance(test_item, HTTPTest):
                proto_http_test_args = {
                    "path": test_item.path,
                    "method": http_method_map[test_item.method],
                }
                if test_item.headers:
                    proto_http_test_args["headers"] = test_item.headers
                if test_item.body is not None:
                    proto_http_test_args["body"] = test_item.body

                proto_http_test = ws_pb2.HttpTest(**proto_http_test_args)
                proto_http_tests.append(proto_http_test)
            elif isinstance(test_item, BrowserTest):
                proto_browser_test = ws_pb2.BrowserTest(
                    workflow_steps=test_item.workflow_steps
                )
                proto_browser_tests.append(proto_browser_test)

        proto_test_config = ws_pb2.TestConfig(
            http_tests=proto_http_tests, browser_tests=proto_browser_tests
        )

        return ws_pb2.VerificationRequest(push_id=sync_id, tests=proto_test_config)

    async def handle_verify_request(
        self,
        websocket: websockets.ClientConnection,
        verify_future: VerifyFuture,
    ):
        """
        Handles a push request from the client.
        """
        log.info(f"Starting verify process for future {id(verify_future)}...")

        if verify_future.done():
            log.warning(
                f"Verify future {id(verify_future)} was already done before processing started."
            )
            return

        # Convert VerifyRequest dataclass to protobuf VerificationRequest
        proto_verification_req = self._convert_verify_request_to_proto(
            verify_future.sync_id, verify_future.verify_request
        )

        ws_msg = ws_pb2.WebsocketMessage(
            message_type=ws_pb2.WebsocketMessage.MessageType.VERIFICATION_REQUEST,
            verification_request=proto_verification_req,
        )

        log.info("Sending verify request...")
        await websocket.send(ws_msg.SerializeToString())

        # Wait for the response from the proxy
        response_bytes = await asyncio.wait_for(
            websocket.recv(), timeout=RESPONSE_TIMEOUT
        )
        response_msg = ws_pb2.WebsocketMessage()
        response_msg.ParseFromString(response_bytes)

        msg_type = response_msg.message_type
        log.info(f"Received response from proxy: type={msg_type}")

        if msg_type == ws_pb2.WebsocketMessage.MessageType.VERIFICATION_RESPONSE:
            status = response_msg.verification_response.status
            log.info(f"Verify status: {VerificationStatusPb.Name(status)}")
            if status == VerificationStatusPb.IN_PROGRESS:
                if not verify_future.done():
                    verify_future.set_result(
                        VerifyResult(verify_future.sync_id, "in_progress")
                    )
            else:
                err_msg = f"Verify failed with status {VerificationStatusPb.Name(status)}: {response_msg.verification_response.error_message}"
                log.error(err_msg)
                if not verify_future.done():
                    verify_future.set_exception(RuntimeError(err_msg))
        else:
            err_msg = f"Received unexpected message type {ws_pb2.WebsocketMessage.MessageType.Name(response_msg.message_type)}"
            "from proxy (instead of VERIFICATION_RESPONSE)"
            log.error(err_msg)
            if not verify_future.done():
                verify_future.set_exception(RuntimeError(err_msg))
