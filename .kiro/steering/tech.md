# Tech Stack

## Backend
- **Python 3.10+**
- **FastAPI** — REST API framework
- **Pydantic v2** — data models and JSON serialization (`model_dump_json`, `model_validate_json`)
- **Redis** — game state persistence (7-day TTL, key pattern `session:<email>`), OTP storage, session tokens, rate limiting
- **python-dotenv** — loads Redis and Gmail credentials from `.env`
- **uvicorn** — ASGI server
- **smtplib** (stdlib) — sends OTP emails via Gmail SMTP (STARTTLS, port 587)

## Authentication
- **Email-based OTP** — players identify with email; 6-digit code sent via Gmail
- **Bearer token auth** — opaque UUID token issued after OTP verification, stored in Redis (`token:<uuid>` → email, 24h TTL)
- **BearerTokenMiddleware** — enforces auth on all `/api/` routes except `/api/auth/*`
- **Rate limiting** — 3 OTP requests per email per 60 seconds (`ratelimit:<email>`)

## Frontend
- **Vanilla HTML / CSS / JavaScript** — no build step, no frameworks
- **marked** (CDN) — parses the in-game manual markdown at load time
- Static files served by FastAPI's `StaticFiles` mount at `/static`
- **Login UI** — `#login-overlay` shown before game canvas; stores token in `localStorage` as `cc_token`

## Testing
- **pytest** — test runner
- **hypothesis** — property-based testing (13 correctness properties)
- **fakeredis** — in-memory Redis for tests (no real Redis needed in CI)
- **httpx** — async test client for FastAPI

## Pinned Dependencies
See `requirements.txt`. Key versions:
- `fastapi==0.110.0`
- `uvicorn==0.28.0`
- `redis==5.0.1`
- `python-dotenv==1.0.1`
- `pydantic[email]==2.6.4`
- `pytest==8.1.0`
- `hypothesis==6.100.0`
- `fakeredis==2.21.0`

## Environment Variables (`.env`)
```
REDIS_HOST=...
REDIS_PORT=...
REDIS_PASSWORD=...
REDIS_USER=default
BASE_PATH=          # optional; set to path prefix when behind a reverse proxy
GMAIL_ADDRESS=...           # Gmail account for sending OTP emails
GMAIL_APP_PASSWORD=...      # App Password (requires 2FA enabled on Google account)
```

The app raises `RuntimeError` at startup if `GMAIL_ADDRESS` or `GMAIL_APP_PASSWORD` are absent.

## Redis Key Scheme

| Key pattern | TTL | Contents |
|---|---|---|
| `session:<email>` | 604800 s (7 days) | GameState JSON |
| `otp:<email>` | 300 s (5 min) | 6-digit OTP |
| `token:<uuid>` | 86400 s (24 h) | email address |
| `ratelimit:<email>` | 60 s | integer hit count |

## Common Commands

```bash
# First-time setup
./setup.sh              # creates ./venv and installs requirements.txt

# Server lifecycle
./manage.sh start       # launch server in background (logs → app.log, PID → .app.pid)
./manage.sh stop        # stop background server
./manage.sh restart     # stop then start
./manage.sh status      # check if running

# Manual run (foreground, with reload)
source venv/bin/activate
python src/main.py      # starts uvicorn on http://127.0.0.1:8000 with --reload

# Run tests
source venv/bin/activate
pytest tests/           # runs all unit + property-based tests
pytest tests/test_otp_login_properties.py  # property tests only
```

## Known Issues
- `manage.sh` has a hardcoded `APP_DIR` path that does not match this checkout — update it before use.
