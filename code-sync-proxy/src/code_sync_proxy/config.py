import logging
import uuid

import json_log_formatter
from pydantic import Field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    # Websocket manager settings
    websocket_redis_ttl_seconds: int = Field(default=(60 * 60 * 2))
    worker_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # Redis Settings
    redis_enabled: bool = Field(default=False)
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_db: int = Field(default=0)
    redis_password: str | None = Field(default=None)

    # Proxy Auth
    proxy_api_key: str = Field(default="your-secret-api-key")


settings = Settings()


def _init_logging():
    """Initialize logging with a JSON format to stdout."""
    formatter = json_log_formatter.JSONFormatter()
    json_handler = logging.StreamHandler()
    json_handler.setFormatter(formatter)
    # Using a generic logger name for the proxy
    logger = logging.getLogger("code_sync_proxy")
    logger.addHandler(json_handler)
    logger.setLevel(logging.INFO)
    # Also configure root logger for FastAPI/Uvicorn logs if needed
    logging.basicConfig(handlers=[json_handler], level=logging.INFO)


def init_config():
    _init_logging()
