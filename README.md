# Belgrade Bus Bot

A Telegram bot for checking live bus arrivals in Belgrade using the Beograd Plus public transport API. Supports real-time arrival times, station search with Serbian character normalization, and per-user favorite stops.

## Features

- **Live bus arrivals** — real-time data from the Beograd Plus API
- **Station search** — search by name with or without Serbian special characters (č, ć, š, ž, đ)
- **Favorite stops** — save your most used stations per user
- **Per-user data** — every user's favorites are completely separate
- **Line filtering** — check only the specific bus lines you care about

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and usage guide |
| `/search [name]` | Search for a station by name, e.g. `/search Zeleni venac` |
| `/check [station_id or name] [lines]` | Live arrivals for a station filtered by line, e.g. `/check 465 58 74` |
| `/save [name] [station_id]` | Save a favorite stop, e.g. `/save home 465` |
| `/favorites` | List all your saved favorite stops |
| `/delete [name]` | Remove a favorite stop |

## Setup

### Prerequisites
- Python 3.8+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### Installation

1. Clone the repo:
```bash
git clone https://github.com/yourusername/belgrade-bus-bot
cd belgrade-bus-bot
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
