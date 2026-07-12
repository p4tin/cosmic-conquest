# Project Structure

```
cosmic-conquest/
├── src/
│   ├── main.py        # FastAPI app, Game class, Pydantic models, game REST endpoints
│   ├── auth.py        # Auth router (/api/auth/*), BearerTokenMiddleware, Pydantic auth models
│   ├── auth_utils.py  # Pure functions: OTP generation, rate limiting, token lifecycle
│   ├── email_sender.py # Gmail SMTP OTP delivery
│   └── cylon.py       # CylonAI class — enemy production, expansion, combat, relic buffs
├── static/
│   ├── index.html     # Page shell, login overlay, HUD, map canvas, modals
│   ├── index.css      # All styles — CRT theme, layout, login UI, modals, difficulty
│   └── index.js       # Login flow, keyboard handler, API client, render loop, manual
├── tests/
│   ├── __init__.py
│   ├── test_otp_login_properties.py  # 13 Hypothesis property-based tests
│   ├── test_otp_login_unit.py        # Unit tests for auth edge cases
│   └── test_validate_endpoint.py     # Targeted tests for /api/auth/validate
├── .env               # Redis + Gmail credentials (not committed)
├── requirements.txt   # Pinned Python dependencies
├── setup.sh           # Creates venv and installs requirements
└── manage.sh          # Background server lifecycle (start/stop/restart/status)
```

## Key Architectural Boundaries

### Authentication Layer (`src/auth.py`, `src/auth_utils.py`, `src/email_sender.py`)
- **`auth.py`** — FastAPI router (`/api/auth/request-otp`, `/api/auth/verify-otp`, `/api/auth/validate`) + `BearerTokenMiddleware`. The Redis client is injected via `set_redis()` called from `main.py`.
- **`auth_utils.py`** — pure functions (`generate_otp`, `store_otp`, `verify_and_consume_otp`, `check_and_increment`, `issue_token`, `resolve_token`). All take a Redis client as first arg — no global state.
- **`email_sender.py`** — `send_otp_email(to_address, otp)` connects to Gmail SMTP, raises on failure.
- **`BearerTokenMiddleware`** — Starlette `BaseHTTPMiddleware`. Skips `/api/auth/*` and non-`/api/` paths. Resolves token → email and injects `request.state.player_email`.

### Backend (`src/main.py`)
- **`Game`** — in-memory state container. Owns `galaxy` (dict keyed by `(int, int)` tuples), `player`, `cylon_ai`, and hunter state. All game actions are methods on this class (`move`, `fight`, `colonize`, `build`, `pulse`, `repair`, `scrap`). Every action ends with `end_turn()`.
- **`GameState`** (Pydantic) — the serializable projection of `Game`. Galaxy keys are converted from `(x, y)` tuples to `"x,y"` strings for JSON compatibility. This is the only thing stored in Redis and returned from every endpoint.
- **`to_state` / `load_from_state`** — the only bridge between `Game` and `GameState`. Always use these; never construct one from the other directly.
- **`get_game(email)` / `save_game(email, game)`** — Redis read/write helpers. Key is `session:<email>` with 7-day TTL.
- **Endpoints** — thin wrappers: read `request.state.player_email` → load game → call one `Game` method → save → return `to_state()`. No business logic lives in endpoints.
- **`PrefixMiddleware`** — strips `BASE_PATH` from request paths when running behind a reverse proxy. Only active when `BASE_PATH` env var is set.
- **`validate_env()`** — startup hook that raises `RuntimeError` if `GMAIL_ADDRESS` or `GMAIL_APP_PASSWORD` are missing.

### Cylon AI (`src/cylon.py`)
- **`CylonAI`** — stateless per turn. Reads `DIFFICULTY_PRESETS` at init. `process_turn(galaxy, player)` mutates the shared `galaxy` dict in place and returns a log string.
- Difficulty presets live in `CylonAI.DIFFICULTY_PRESETS` — the single source of truth for all difficulty tuning values.
- The AI is only allowed to operate on sectors that existed before its turn started. `end_turn()` in `main.py` enforces this by snapshotting valid keys and deleting any new ones the AI created outside bounds.

### Frontend (`static/`)
- Single-page app. All state comes from the last API response (`GameState`).
- **Login overlay** (`#login-overlay`) — shown before game canvas when no valid token exists in `localStorage`. Two-step flow: email input → OTP input. CRT aesthetic.
- **Token management** — stored in `localStorage` as `cc_token`. Sent as `Authorization: Bearer <token>` header on every `/api/` request. Validated on page load via `GET /api/auth/validate`.
- No local state mutation — every player action calls an API endpoint and re-renders from the response.
- The manual markdown is embedded as a JS template literal in `index.js` and parsed at load time via the `marked` CDN.

## Conventions
- Galaxy sectors use `(int, int)` tuple keys internally; `"x,y"` string keys in JSON/Redis.
- All game log messages are UPPERCASE and terse (military terminal aesthetic).
- Anomaly sectors (`black_hole`, `nebula`, `relic`) always have `owner="Neutral"` and are never capturable by either side.
- Infrastructure is a single string field per sector (`"shipyard"`, `"sensor"`, `"battery"` or `None`) — one building per sector.
- Player identity is email-based. Redis game state key is `session:<email>` with 7-day TTL.
- Auth tokens are opaque UUIDs. Redis key is `token:<uuid>` → email with 24h TTL.
- OTPs are 6-digit numeric strings. Redis key is `otp:<email>` with 5-minute TTL.
- All auth endpoints live under `/api/auth/` and are exempt from the BearerTokenMiddleware.
