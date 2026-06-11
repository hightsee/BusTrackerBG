# Oracle Always Free Deployment

This deployment path runs the Vite frontend as static files through Caddy and the Flask API through Waitress on `127.0.0.1:5000`.

## Server Packages

On an Ubuntu Oracle Always Free VM:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm caddy git
```

Node.js from Ubuntu may be old. If `npm run build` fails because Vite requires a newer Node version, install Node.js 20 LTS from NodeSource before building.

## App User And Directories

```bash
sudo useradd --system --create-home --home-dir /opt/bustrackerbg --shell /usr/sbin/nologin bustracker
sudo mkdir -p /opt/bustrackerbg/app /var/lib/bustrackerbg
sudo chown -R bustracker:bustracker /opt/bustrackerbg /var/lib/bustrackerbg
```

Clone or copy this repo into `/opt/bustrackerbg/app`.

## Build

```bash
cd /opt/bustrackerbg/app
sudo -u bustracker python3 -m venv .venv
sudo -u bustracker .venv/bin/pip install -r requirements.txt
cd frontend
sudo -u bustracker npm ci
sudo -u bustracker npm run build
```

## Environment

Copy `deploy/oracle/env.example` to `/etc/bustrackerbg.env`, replace the domain, and generate a strong `JWT_SECRET`.

```bash
sudo cp deploy/oracle/env.example /etc/bustrackerbg.env
sudo nano /etc/bustrackerbg.env
sudo chmod 600 /etc/bustrackerbg.env
```

`DATA_DIR=/var/lib/bustrackerbg` keeps `gtfs.db` and `app_data.db` on persistent VM storage. The GTFS zip refresh and BG Prevoz scraper/import run as separate scheduled jobs inside the API process. The BG Prevoz job checks the site on schedule and skips DB replacement for lines whose scraped content hash has not changed.

## Systemd

```bash
sudo cp deploy/oracle/bustrackerbg.service.example /etc/systemd/system/bustrackerbg.service
sudo systemctl daemon-reload
sudo systemctl enable --now bustrackerbg
sudo systemctl status bustrackerbg
```

## Caddy

Copy `deploy/oracle/Caddyfile.example` into `/etc/caddy/Caddyfile`, replace `YOUR_DOMAIN_HERE`, and reload Caddy.

```bash
sudo cp deploy/oracle/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Point your domain A record at the Oracle VM public IP. Caddy will request HTTPS certificates automatically once DNS and firewall rules are correct.

## Oracle Firewall

Open ports `80` and `443` in:

- Oracle VCN security list or network security group
- Ubuntu firewall, if enabled

Keep the Flask API bound to `127.0.0.1`; do not expose port `5000` publicly.

## Verification

```bash
curl -s http://127.0.0.1:5000/api/health
curl -s https://YOUR_DOMAIN_HERE/api/health
```

Then test the public site in a browser:

- Icons render.
- Stop search works.
- Address search works.
- Route lookup works.
- Login/favorites work.
