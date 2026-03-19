# Belgrade Bus Bot

A Telegram bot for checking live bus arrivals in Belgrade using the Beograd Plus public transport API. Supports real-time arrival times, station search with Serbian character normalization, and per-user favorite stops.

## Features

- **Live bus arrivals** — real-time data with improved grouping by bus line
- **Planned timetables** — offline schedules from Belgrade GTFS data (data.gov.rs)
- **Station search** — search by name with or without Serbian special characters (č, ć, š, ž, đ)
- **Favorite stops** — save your most used stations per user
- **Per-user data** — every user's favorites are completely separate
- **Line filtering** — check only the specific bus lines you care about

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and usage guide |
| `/search [name]` | Search for a station by name, e.g. `/search Zeleni venac` |
| `/check [station_id or name] [lines]` | Live arrivals for a station, e.g. `/check 465 58 74` |
| `/timetable [line]` | Planned schedule for a specific line, e.g. `/timetable 58` |
| `/save [name] [station_id]` | Save a favorite stop, e.g. `/save home 465` |
| `/favorites` | List all your saved favorite stops |
| `/delete [name]` | Remove a favorite stop |

### Admin Commands
| Command | Description |
|---|---|
| `/users` | List all registered users and their start dates |
| `/timetablestatus` | Check GTFS database health and last update time |
| `/refreshtimetable` | Manually trigger a GTFS data refresh from data.gov.rs |

## Setup

### Prerequisites
- Python 3.8+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### Bot Access
You can add the bot to your telegram through the [Telegram Link](t.me/BusTrackerBG_bot)

If you want to run it yourself follow the installation below

|
v

### Installation

1. Clone the repo:
```bash
git clone https://github.com/hightsee/BusTrackerBG
cd BusTrackerBG
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create your `.env` file:
```bash
cp .env.example .env
```

4. Fill in your `.env` file:
```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
```

5. Run the bot:
```bash
python bus_bot.py
```

## Environment Variables

See `.env.example` for all required variables.

## How It Works

The Beograd Plus API uses AES-256-CBC encryption for its requests and responses. This bot handles the encryption and decryption automatically using the publicly available API credentials from the [bgpp](https://github.com/MikMik1011/bgpp) open source project.

## Credits

- API reverse engineering by [MikMik1011](https://github.com/MikMik1011/bgpp)
- Live transport data provided by [Beograd Plus](https://www.beograd.rs)

## License

MIT
