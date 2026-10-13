"""Configuration, read from KIMAGETOOLS_* environment variables."""

import os
import tempfile
from pathlib import Path


def _env(name, default):
    return os.environ.get(f"KIMAGETOOLS_{name}", default)


class Config:
    LOG_LEVEL = _env("LOG_LEVEL", "INFO")

    MAX_UPLOAD_MB = int(_env("MAX_MB", "25"))
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

    # Rate limiting (Flask-Limiter). One shared budget per client IP across all
    # routes; static files are exempt (see create_app).
    RATELIMIT_APPLICATION = _env("RATE_LIMIT", "30 per minute")
    # Use e.g. redis://localhost:6379 so limits are shared across worker processes.
    RATELIMIT_STORAGE_URI = _env("RATELIMIT_STORAGE", "memory://")
    RATELIMIT_HEADERS_ENABLED = True

    # Number of reverse proxies in front of the app (e.g. 1 for nginx), so rate
    # limiting sees the real client IP instead of the proxy's.
    TRUSTED_PROXIES = int(_env("TRUSTED_PROXIES", "0"))

    # Temp files: uploads and results live here only while a request is being
    # handled. A background sweeper deletes anything older than TEMP_TTL_SECONDS.
    TEMP_DIR = Path(_env("TEMP_DIR", Path(tempfile.gettempdir()) / "kimagetools"))
    TEMP_TTL_SECONDS = int(_env("TEMP_TTL", "300"))
    TEMP_SWEEP_INTERVAL_SECONDS = float(_env("TEMP_SWEEP_INTERVAL", "60"))
