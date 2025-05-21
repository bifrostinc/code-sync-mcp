from code_sync_proxy.pb import ws_pb2

# Message type enums for cleaner reference
PushStatusPb = ws_pb2.PushResponse.PushStatus
VerificationStatusPb = ws_pb2.VerificationResponse.VerificationStatus


class MessageFactory:
    @staticmethod
    def create_push_error(error_message: str) -> ws_pb2.WebsocketMessage:
        return ws_pb2.WebsocketMessage(
            message_type=ws_pb2.WebsocketMessage.MessageType.PUSH_RESPONSE,
            push_response=ws_pb2.PushResponse(
                status=PushStatusPb.FAILED,
                error_message=error_message,
            ),
        )

    @staticmethod
    def create_verification_error(error_message: str) -> ws_pb2.WebsocketMessage:
        return ws_pb2.WebsocketMessage(
            message_type=ws_pb2.WebsocketMessage.MessageType.VERIFICATION_RESPONSE,
            verification_response=ws_pb2.VerificationResponse(
                status=VerificationStatusPb.FAILED,
                error_message=error_message,
            ),
        )

    @staticmethod
    def create_verification_in_progress() -> ws_pb2.WebsocketMessage:
        return ws_pb2.WebsocketMessage(
            message_type=ws_pb2.WebsocketMessage.MessageType.VERIFICATION_RESPONSE,
            verification_response=ws_pb2.VerificationResponse(
                status=VerificationStatusPb.IN_PROGRESS,
            ),
        )
