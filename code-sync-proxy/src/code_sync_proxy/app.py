from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import init_config
from .routers import ws

init_config()

api = FastAPI(title="Code Sync Proxy API")

# Configure CORS
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
api.include_router(ws.router)


@api.get("/")
async def root():
    return {"message": "Welcome to the Code Sync Proxy API"}


@api.get("/health")
async def health():
    return {"status": "ok"}
