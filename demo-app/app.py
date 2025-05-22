from fastapi import FastAPI
import os
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)

app = FastAPI(title="Code Sync Proxy Demo App")


def configure_code_sync_tags():
    """Here you would configure the tags with the latest version of the code."""
    log.info(f"Running with push_id: {os.environ.get('BIFROST_PUSH_ID')}")


configure_code_sync_tags()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"message": "Hello, World!"}
