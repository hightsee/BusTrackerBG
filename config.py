import logging
import os
import secrets
from dotenv import load_dotenv
load_dotenv()


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)

def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)

APP_ENV = os.getenv("APP_ENV", "local").strip().lower()
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "5000"))
IS_LOCAL_DEV = APP_ENV in {"local", "dev", "development", "test"} and _is_loopback_host(API_HOST)

DATA_DIR = os.getenv("DATA_DIR", "").strip()
if DATA_DIR:
    DATA_DIR = os.path.abspath(os.path.expanduser(DATA_DIR))
    os.makedirs(DATA_DIR, exist_ok=True)

def _data_path(env_name: str, default_name: str) -> str:
    configured = os.getenv(env_name, "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    if DATA_DIR:
        return os.path.join(DATA_DIR, default_name)
    return default_name

JWT_SECRET = os.getenv("JWT_SECRET", "").strip()

if not JWT_SECRET:
    if not IS_LOCAL_DEV:
        raise RuntimeError(
            "JWT_SECRET must be set outside local loopback development. "
            "Refusing to start with an ephemeral signing key."
        )
    logging.warning(
        "No JWT_SECRET found in local development; generating a random session secret. "
        "JWT tokens will NOT persist across restarts."
    )
    JWT_SECRET = secrets.token_hex(32)

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174",
).split(",")
RATE_LIMIT_STORAGE_URI = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://").strip() or "memory://"
TRUST_PROXY = _env_bool("TRUST_PROXY", False)
GTFS_UPDATE_INTERVAL_DAYS = max(1, int(os.getenv("GTFS_UPDATE_INTERVAL_DAYS", "7")))
GTFS_UPDATE_HOUR = max(0, min(23, int(os.getenv("GTFS_UPDATE_HOUR", "3"))))
GTFS_UPDATE_MINUTE = max(0, min(59, int(os.getenv("GTFS_UPDATE_MINUTE", "0"))))
BGPREVOZ_UPDATE_ENABLED = _env_bool("BGPREVOZ_UPDATE_ENABLED", True)
BGPREVOZ_UPDATE_INTERVAL_DAYS = _env_int("BGPREVOZ_UPDATE_INTERVAL_DAYS", 1, 1)
BGPREVOZ_UPDATE_HOUR = max(0, min(23, int(os.getenv("BGPREVOZ_UPDATE_HOUR", "4"))))
BGPREVOZ_UPDATE_MINUTE = max(0, min(59, int(os.getenv("BGPREVOZ_UPDATE_MINUTE", "30"))))
BGPREVOZ_IMPORT_DELAY_SECONDS = _env_float("BGPREVOZ_IMPORT_DELAY_SECONDS", 0.2, 0.0)
APP_DATA_DB = _data_path("APP_DATA_DB", "app_data.db")
GTFS_DATASET_PAGE_URL = os.getenv(
    "GTFS_DATASET_PAGE_URL",
    "https://data.gov.rs/sr/datasets/gtfs/",
).strip()
GTFS_DB = _data_path("GTFS_DB", "gtfs.db")
GTFS_MAX_DOWNLOAD_BYTES = _env_int("GTFS_MAX_DOWNLOAD_BYTES", 128 * 1024 * 1024, 1)
GTFS_MAX_UNCOMPRESSED_BYTES = _env_int("GTFS_MAX_UNCOMPRESSED_BYTES", 512 * 1024 * 1024, 1)
GTFS_MAX_FILE_BYTES = _env_int("GTFS_MAX_FILE_BYTES", 256 * 1024 * 1024, 1)

if RATE_LIMIT_STORAGE_URI == "memory://" and not _is_loopback_host(API_HOST):
    raise RuntimeError(
        "RATE_LIMIT_STORAGE_URI=memory:// is only allowed when API_HOST is loopback. "
        "Set a shared backend such as Redis before binding the API publicly."
    )
