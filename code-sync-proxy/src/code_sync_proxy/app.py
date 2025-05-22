from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect

from .ws.manager import default_manager
from .config import init_config

import logging

log = logging.getLogger(__name__)


init_config()

api = FastAPI(title="Code Sync Proxy API")


@api.get("/")
async def root():
    return {"message": "Welcome to the Code Sync Proxy API"}


@api.get("/health")
async def health():
    return {"status": "ok"}


@api.websocket("/api/v1/push/ide/{app_id}/{deployment_id}")
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


@api.websocket("/api/v1/push/sidecar/{app_id}/{deployment_id}")
async def sidecar_endpoint(websocket: WebSocket, app_id: str, deployment_id: str):
    """WebSocket endpoint for sidecar connections."""
    log.info(f"Sidecar client {app_id}/{deployment_id} connected")
    await websocket.accept()
    try:
        await default_manager.attach_sidecar(app_id, deployment_id, websocket)
    except WebSocketDisconnect:
        log.info(f"Sidecar {app_id}/{deployment_id} disconnected")
    except Exception as e:
        log.exception(f"Error in sidecar endpoint: {e}")
        if websocket.client_state.value != 3:  # Not already closed
            await websocket.close(code=1011)  # Internal server error


@api.get("/api/v1/push/ide/{app_id}/{deployment_id}/ready")
async def check_sidecar_ready(app_id: str, deployment_id: str):
    """Check if a sidecar is ready for the specified app/deployment."""
    is_ready = default_manager.is_sidecar_ready(app_id, deployment_id)
    return {"ready": is_ready}
