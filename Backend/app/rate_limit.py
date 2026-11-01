"""Per-client rate limiting.

In-memory by default. When running more than one backend instance, point
RATE_LIMIT_STORAGE_URI at a shared store, e.g. redis://redis:6379/0.
Client IPs come from request.client, so run uvicorn with --proxy-headers behind a proxy.
"""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("RATE_LIMIT_STORAGE_URI", "memory://"),
)


def chat_rate_limit() -> str:
    return get_settings().rate_limit
