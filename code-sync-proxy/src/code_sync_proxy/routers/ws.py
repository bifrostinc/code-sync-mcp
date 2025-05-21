from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import logging

from code_sync_proxy.ws.manager import default_manager

router = APIRouter(prefix="/api/v1/push", tags=["WebSocket"])
log = logging.getLogger(__name__)


@router.websocket("/ide/{app_id}/{deployment_id}")
async def ide_push_endpoint(websocket: WebSocket, app_id: str, deployment_id: str):
    """WebSocket endpoint for IDE connections."""
    await websocket.accept()
    try:
        await default_manager.attach_ide(app_id, deployment_id, websocket)
    except WebSocketDisconnect:
        log.info(f"IDE client {app_id}/{deployment_id} disconnected")
    except Exception as e:
        log.exception(f"Error in IDE endpoint: {e}")
        if websocket.client_state.value != 3:  # Not already closed
            await websocket.close(code=1011)  # Internal server error


@router.websocket("/sidecar/{app_id}/{deployment_id}")
async def sidecar_endpoint(websocket: WebSocket, app_id: str, deployment_id: str):
    """WebSocket endpoint for sidecar connections."""
    await websocket.accept()
    try:
        await default_manager.attach_sidecar(app_id, deployment_id, websocket)
    except WebSocketDisconnect:
        log.info(f"Sidecar {app_id}/{deployment_id} disconnected")
    except Exception as e:
        log.exception(f"Error in sidecar endpoint: {e}")
        if websocket.client_state.value != 3:  # Not already closed
            await websocket.close(code=1011)  # Internal server error


@router.get("/ide/{app_id}/{deployment_id}/ready")
async def check_sidecar_ready(app_id: str, deployment_id: str):
    """Check if a sidecar is ready for the specified app/deployment."""
    is_ready = default_manager.is_sidecar_ready(app_id, deployment_id)
    return {"ready": is_ready}