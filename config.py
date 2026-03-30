import os
from dotenv import load_dotenv
load_dotenv()

API_KEY = "1688dc355af72ef09287"
BASE_URL = "https://announcement-bgnaplata.ticketing.rs"
AES_KEY_B64 = "3+Lhz8XaOli6bHIoYPGuq9Y8SZxEjX6eN7AFPZuLCLs="
AES_IV_B64 = "IvUScqUudyxBTBU9ZCyjow=="
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BOT_DATA_DB = "bot_data.db"
GTFS_URL = "https://data.gov.rs/s/resources/gradski-javni-prevoz-u-beogradu-gtfs/20251031-111721/bgprev-belgrade-rs-2-.zip"
GTFS_DB = "gtfs.db"

