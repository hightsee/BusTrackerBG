# Oracle A1 Deployment Guide

This guide deploys BusTrackerBG on an Oracle Cloud `VM.Standard.A1.Flex` instance using Nginx for public traffic and Flask/Waitress on localhost.

## Instance Checklist

When creating the instance in Oracle Cloud:

- Shape: `VM.Standard.A1.Flex`
- Start small if capacity is tight: `1 OCPU` and `4 GB` RAM is enough to start.
- Image: Ubuntu 24.04 or Ubuntu 22.04.
- VCN: use the same VCN as your existing working instance if possible.
- Subnet: public subnet.
- Primary VNIC: assign public IPv4 address.
- Private IPv4: automatic.
- Skip source/destination check: unchecked.
- Network security groups: optional; leave disabled unless your setup already uses them.
- Boot volume: default is fine; `50 GB` is enough.
- Keep the existing E2 Micro until the A1 is reachable over SSH.

Avoid paid extras:

- No paid load balancer.
- No capacity reservation.
- No extra block volumes unless you understand the free limits.
- No public port for Flask `5000` or Vite `5173`.

## Network Rules

In the OCI security list or NSG, allow:

- TCP `22` from your IP if possible, otherwise temporarily from `0.0.0.0/0`.
- TCP `80` from `0.0.0.0/0`.
- TCP `443` from `0.0.0.0/0`.

Default outbound internet access should remain enabled because the server needs package installs, GTFS updates, and geocoding requests.

Target public layout:

```text
Internet -> Oracle public IP -> Nginx :80/:443 -> Flask 127.0.0.1:5000
```

## SSH Into The Instance

For Ubuntu:

```bash
ssh ubuntu@<A1_PUBLIC_IP>
```

For Oracle Linux:

```bash
ssh opc@<A1_PUBLIC_IP>
```

The rest of this guide assumes Ubuntu and the `ubuntu` user. If using Oracle Linux, replace `/home/ubuntu` and `User=ubuntu` with the correct user/home path.

## Install System Packages

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git nginx python3-venv python3-pip nodejs npm
```

If the Ubuntu Node.js package is too old for Vite, install Node 20 instead before running frontend commands.

## Enable Instance Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Do not allow public access to ports `5000` or `5173`.

## Clone The App

```bash
git clone <your-github-repo-url>
cd BusTrackerBG
```

Create the environment file:

```bash
cp .env.example .env
nano .env
```

Set at least:

```env
APP_ENV=production
JWT_SECRET=<long-random-secret>
API_HOST=127.0.0.1
API_PORT=5000
ALLOWED_ORIGINS=http://<A1_PUBLIC_IP>
```

When you later add a domain, update `ALLOWED_ORIGINS` to include the domain origin, for example `https://example.com`.

## Backend Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Test the API manually:

```bash
python3 api.py
```

In another SSH session:

```bash
curl http://127.0.0.1:5000/api/health
```

Stop the manual API process before creating the systemd service.

## Frontend Build

```bash
cd frontend
npm install
npm run build
cd ..
```

Nginx will serve the built files from `frontend/dist`.

## Create Backend Service

Create the service file:

```bash
sudo nano /etc/systemd/system/bustracker.service
```

Use:

```ini
[Unit]
Description=BusTrackerBG Flask API
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/BusTrackerBG
EnvironmentFile=/home/ubuntu/BusTrackerBG/.env
ExecStart=/home/ubuntu/BusTrackerBG/.venv/bin/waitress-serve --host=127.0.0.1 --port=5000 wsgi:app
Restart=always
User=ubuntu

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bustracker
sudo systemctl status bustracker
```

Check logs:

```bash
journalctl -u bustracker -f
```

## Configure Nginx

Create the site:

```bash
sudo nano /etc/nginx/sites-available/bustracker
```

Use:

```nginx
server {
    listen 80;
    server_name _;

    root /home/ubuntu/BusTrackerBG/frontend/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:5000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        try_files $uri /index.html;
    }
}
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/bustracker /etc/nginx/sites-enabled/bustracker
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Open:

```text
http://<A1_PUBLIC_IP>
```

## Optional Domain And HTTPS

A domain is optional. You can use the public IP directly, but a domain is cleaner and makes HTTPS easier.

With a domain:

1. Point an `A` record to the Oracle public IP.
2. Change Nginx `server_name _;` to your domain.
3. Add the domain to `ALLOWED_ORIGINS`.
4. Install HTTPS with Let's Encrypt:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <your-domain>
```

## Updating The App

```bash
cd /home/ubuntu/BusTrackerBG
git pull
source .venv/bin/activate
pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
sudo systemctl restart bustracker
sudo systemctl reload nginx
```

## Troubleshooting

Check backend:

```bash
sudo systemctl status bustracker
journalctl -u bustracker -n 100
curl http://127.0.0.1:5000/api/health
```

Check Nginx:

```bash
sudo nginx -t
sudo systemctl status nginx
curl http://127.0.0.1/
```

Check public access:

```bash
curl http://<A1_PUBLIC_IP>/api/health
```

If SSH works but the website does not load, check both OCI ingress rules and `ufw`.
