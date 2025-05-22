from code_sync_proxy.pb import ws_pb2

# Message type enums for cleaner reference
PushStatusPb = ws_pb2.PushResponse.PushStatus


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
