# AutoCheck

AutoCheck is a self-hosted SPA and FastAPI service for daily account check-ins. It stores account passwords, session cookies, and authenticated proxy credentials encrypted in SQLite, and it supports HTTP, HTTPS, and SOCKS5 proxies per account.

The original `account.txt` and `proxy.txt` files are imported into SQLite on first startup when the relevant table is empty. They are intentionally ignored by Git. Accounts and proxies can be managed after that from the browser.

## Local run

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
cp .env.example .env
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8010 --reload --env-file .env
```

Open `http://127.0.0.1:8010`. The first protected startup creates a random administrator password in `data/.admin_password`. Set `AUTOCHECK_ADMIN_PASSWORD` in `.env` before production deployment instead. Set and retain `AUTOCHECK_ENCRYPTION_KEY` as well; changing it makes existing encrypted passwords, cookies, and proxy credentials unreadable.

## Site configuration

Use **Site settings** to set the base URL, login/check-in/balance paths, selectors for the target site's login form, and extra authenticated request headers. A path can also be a full URL. The default values match the supplied templates. Set an account's optional **User ID** when the target requires a `new-api-user` request header.

Each account has its own daily enable switch, hour, minute, and optional random delay window. One global IANA timezone controls every account schedule and every timestamp displayed by the SPA. Timestamps remain stored in UTC. A delay of 15 minutes runs that account once between its configured time and 15 minutes afterward. Login runs Chromium through the assigned proxy and refreshes the encrypted session cookie. Check-ins and balance checks use the stored cookie, target user ID, and same proxy. Scheduled runs use stored cookies; use the UI's refresh-and-run command when the target invalidates sessions frequently.

The administrator password can be changed in **Site settings**. A successful change persists to `data/.admin_password`, invalidates previously issued access tokens, and returns a replacement token to the current browser session.

## Deployment

The repository includes the exact service and Nginx source files used for this host:

```bash
/usr/bin/python3 -m venv /opt/checker/.service-venv
/opt/checker/.service-venv/bin/pip install -r /opt/checker/requirements.txt
PLAYWRIGHT_BROWSERS_PATH=/opt/checker/.playwright-browsers /opt/checker/.service-venv/bin/playwright install chromium
install -d -o www-data -g www-data -m 700 /opt/checker/data
chown -R www-data:www-data /opt/checker/.playwright-browsers
install -m 640 -o root -g www-data /opt/checker/.env.example /opt/checker/.env
editor /opt/checker/.env
install -m 644 /opt/checker/deploy/systemd/autocheck.service /etc/systemd/system/autocheck.service
install -m 644 /opt/checker/deploy/nginx/autocheck.i8s.top.conf /etc/nginx/conf.d/autocheck.i8s.top.conf
systemctl daemon-reload
systemctl enable --now autocheck
nginx -t && systemctl reload nginx
```

Issue the certificate after DNS and port 80 are reachable:

```bash
certbot --nginx -d autocheck.i8s.top --redirect
```

Certbot will request its registration email interactively if it has not already been configured. Confirm renewal with `systemctl status certbot.timer` and `certbot renew --dry-run`.

## API

The same-origin API is under `/api`; interactive documentation is at `/api/docs`. All management routes require a bearer token acquired from `POST /api/auth/login`, except `/api/health` and `/api/auth/status`. The SPA handles this automatically.

Important endpoints:

- `GET, POST /api/accounts` and `GET, PATCH, DELETE /api/accounts/{id}`
- `POST /api/accounts/{id}/login`
- `POST /api/accounts/{id}/checkin?refresh_cookie=true`
- `POST /api/checkins/run`
- `POST /api/accounts/{id}/balance?refresh_cookie=true`
- `POST /api/balances/run`
- `GET, POST /api/proxies`, `PATCH, DELETE /api/proxies/{id}`
- `PATCH /api/proxies/assignments`
- `GET, PUT /api/config`
- `GET /api/timezones`
- `GET /api/logs`
- `POST /api/import/legacy`

## CI

GitHub Actions runs the API tests, Python compilation check, and JavaScript syntax check on every push to `main` and on pull requests. The deployed systemd service is intentionally not changed from CI; production deployment remains an explicit server operation.

## Security notes

Do not commit `.env`, `data/`, `account.txt`, or `proxy.txt`. Nginx should be the only public listener; Uvicorn is bound to loopback. Use a unique administrator password, keep the encryption key backed up in a secret manager, and restrict shell access to the server.
