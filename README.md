# Belgrade Bus Bot 🚌

> **⚠️ WARNING: LIVE API CURRENTLY UNAVAILABLE ⚠️**
> The Beograd Plus live public transport API has recently changed and is currently unreachable. **Because of this, live arrivals (the `/check` command) are temporarily disabled.** 
> 
> As a fallback, the bot now runs entirely on offline GTFS data (official schedules). Please use the **`/nextat`** and **`/predict`** commands to view planned departures instead!

A simple Telegram bot for checking bus schedules and tracking public transport in Belgrade.

## Features

- **Offline Schedules** — Since the live API is down, the bot calculates the next departures using official GTFS timetable data.
- **Station Search** — Search for station IDs by name (supports Serbian characters).
- **Favorites** — Save your most-used stations so you don't have to remember their IDs.
- **Line Filtering** — View schedules for specific bus lines only.

## Commands

| Command | Status | Description |
|---|---|---|
| `/start` | 🟢 Active | Welcome message and instructions |
| `/search [name]`| 🟢 Active | Search for a station by name (e.g., `/search Josif`) |
| `/nextat [station] [line]`| 🟢 **Active Main** | Shows the next planned departures for a station |
| `/timetable [line]`| 🟢 Active | Shows the full daily schedule for a specific line |
| `/predict [line]`| 🟢 Active | Shows predicted bus positions based on the timetable |
| `/route [line]`| 🟢 Active | Lists all the stops for a specific bus line |
| `/save [name] [id]`| 🟢 Active | Saves a station to your favorites (e.g., `/save home 182`) |
| `/favorites`| 🟢 Active | Lists all your saved favorite stations |
| `/check` | 🔴 **Disabled** | *Normally shows live arrivals, but the API is currently down.* |
| `/stations` | 🔴 **Disabled** | *Normally shows a live list of stations, currently disabled.* |

## Setup & Running the Bot
You can access the bot on telegram using this link
https://t.me/BusTrackerBG_bot

If you want to run the bot yourself follow the instructions below

### Prerequisites
- Python 3.8+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### Installation

1. Clone the project:
```bash
git clone https://github.com/hightsee/BusTrackerBG
cd BusTrackerBG
```

2. Install the required Python packages:
```bash
pip install -r requirements.txt
```

3. Set up your environment variables:
```bash
cp .env.example .env
```
Open the `.env` file and paste your Telegram bot token next to `BOT_TOKEN=`.

4. Start the bot:
```bash
python bus_bot.py
```

## 🛡️ Production & Security

The API runs using `waitress`, a production-ready server. However, it does not natively support HTTPS.

### Important: Securing Your API
To protect user credentials and JWTs from interception, **you must use a reverse proxy** to handle SSL/TLS:
- **Nginx**: Recommended for Linux servers.
- **Caddy**: Simple and automatic SSL.
- **Cloudflare Tunnel**: Easy to set up if you don't have a static IP.
- **ngrok**: Good for quick testing with HTTPS for development.

**Environment Configuration:**
- Set `API_HOST=127.0.0.1` (default) when using a reverse proxy on the same machine.
- Generate a unique `JWT_SECRET` in your `.env` file (the bot will generate a random one if missing).
- Set `ALLOWED_ORIGINS` to only allow your trusted front-end domains.

## Credits
- Original API reverse engineering by [MikMik1011](https://github.com/MikMik1011/bgpp) 
- Static GTFS Timetable Data provided by [data.gov.rs](https://data.gov.rs/)

## License
MIT
