import os
from dotenv import load_dotenv
load_dotenv()

API_KEY = "1688dc355af72ef09287"
BASE_URL = "https://announcement-bgnaplata.ticketing.rs"
AES_KEY_B64 = "3+Lhz8XaOli6bHIoYPGuq9Y8SZxEjX6eN7AFPZuLCLs="
AES_IV_B64 = "IvUScqUudyxBTBU9ZCyjow=="
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()

# Fallback to BOT_TOKEN if JWT_SECRET is not provided
if not JWT_SECRET:
    JWT_SECRET = BOT_TOKEN

# Security fix: Ensure JWT_SECRET is never empty to prevent forgery
if not JWT_SECRET:
    import secrets
    import logging
    logging.warning("No JWT_SECRET or BOT_TOKEN found! Generating a random secret for this session. "
                    "JWT tokens will NOT persist across restarts.")
    JWT_SECRET = secrets.token_hex(32)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "5000"))
BOT_DATA_DB = "bot_data.db"
GTFS_URL = "https://data.gov.rs/s/resources/gradski-javni-prevoz-u-beogradu-gtfs/20251031-111721/bgprev-belgrade-rs-2-.zip"
GTFS_DB = "gtfs.db"

