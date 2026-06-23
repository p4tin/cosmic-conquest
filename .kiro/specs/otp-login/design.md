# Design Document — OTP Login

## Overview

This design replaces the current anonymous UUID session system with an email-based One-Time Password (OTP) authentication flow. The change has three primary effects:

1. **Identity** — players are now identified by email address rather than a browser-local UUID, giving them persistent game state across devices and browser sessions.
2. **Security** — every game API request must carry a short-lived Bearer token; requests without a valid token are rejected before any game logic runs.
3. **UX** — a two-step login screen (email → OTP code) is displayed before the game canvas, styled consistently with the existing CRT aesthetic.

### Key constraints carried over from requirements

| Concept | Value |
|---|---|
| OTP | 6-digit numeric, TTL 300 s, single-use |
| Session token | opaque UUID, TTL 86 400 s (24 h), Redis key `token:<token>` |
| Game state key | `session:<email>`, TTL 604 800 s (7 days) |
| Rate limit | 3 requests / 60 s per email, Redis key `ratelimit:<email>` |
| SMTP | Gmail, host `smtp.gmail.com`, port 587, STARTTLS |

---

## Architecture

### Component diagram

```
Browser (Vanilla JS)
  │
  │  1. POST /api/auth/request-otp  (email)
  │  2. POST /api/auth/verify-otp   (email + code)
  │  3. GET  /api/auth/validate     (Bearer token)
  │  4. POST /api/<game-action>     (Bearer token)
  ▼
FastAPI  (src/main.py)
  ├── Auth Router  (/api/auth/*)
  │     ├── request_otp()   → RateLimiter → OTPService → EmailSender → Redis
  │     ├── verify_otp()    → OTPService → Redis → TokenService
  │     └── validate()      → TokenService → Redis
  │
  ├── Auth Middleware (BearerTokenMiddleware)
  │     └── applies to all /api/ routes EXCEPT /api/auth/*
  │
  └── Game Router  (/api/*)
        └── existing endpoints — now receive player_email from middleware
              └── Game logic → Redis (session:<email>, TTL 7d)

Redis (external)
  ├── otp:<email>         300 s
  ├── token:<uuid>        86 400 s
  ├── ratelimit:<email>   60 s  (sliding window counter)
  └── session:<email>     604 800 s
```

### Request lifecycle (authenticated game action)

```
Browser → FastAPI → BearerTokenMiddleware
                    ├─ missing header? → 401
                    ├─ token not in Redis? → 401
                    └─ found → inject player_email into request state
                               → Game endpoint → load session:<email> → action → save session:<email>
```

---

## Components and Interfaces

### 1. Auth Router (`src/auth.py` — new file)

Encapsulates all authentication endpoints. Kept in a separate module so game logic in `main.py` stays unchanged.

```python
router = APIRouter(prefix="/api/auth")

@router.post("/request-otp")   # Requirement 1
@router.post("/verify-otp")    # Requirement 2
@router.get("/validate")       # Requirement 8
```

### 2. OTPService

Pure functions responsible for OTP generation and lifecycle.

```python
def generate_otp() -> str:
    """Returns a cryptographically random 6-digit string (zero-padded)."""
    return f"{secrets.randbelow(1_000_000):06d}"

def store_otp(r: redis.Redis, email: str, otp: str) -> None:
    """Writes otp:<email> with TTL=300."""

def verify_and_consume_otp(r: redis.Redis, email: str, submitted: str) -> bool:
    """Reads, compares, and deletes otp:<email>. Returns True on match."""
```

### 3. TokenService

Pure functions for session token lifecycle.

```python
def issue_token(r: redis.Redis, email: str) -> str:
    """Generates UUID, writes token:<uuid> → email, TTL=86400. Returns token."""

def resolve_token(r: redis.Redis, token: str) -> str | None:
    """Returns email for token:<token> or None if absent/expired."""
```

### 4. RateLimiter

```python
def check_and_increment(r: redis.Redis, email: str, limit: int = 3, window: int = 60) -> bool:
    """Returns True if request is allowed; increments counter and sets TTL on first hit.
    Returns False if counter already at/above limit."""
```

### 5. EmailSender (`src/email_sender.py` — new file)

```python
def send_otp_email(to_address: str, otp: str) -> None:
    """
    Connects to smtp.gmail.com:587 via STARTTLS.
    Authenticates with GMAIL_ADDRESS / GMAIL_APP_PASSWORD from env.
    Sends plain-text email with subject 'COSMIC CONQUEST — YOUR ACCESS CODE'.
    Raises SMTPError subclass on failure (caller maps to HTTP 503).
    """
```

### 6. BearerTokenMiddleware

A Starlette `BaseHTTPMiddleware` applied to the FastAPI app. Runs before every request.

```python
class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/auth") or not request.url.path.startswith("/api"):
            return await call_next(request)

        token = _extract_bearer(request.headers.get("Authorization", ""))
        if not token:
            return JSONResponse({"detail": "AUTHENTICATION REQUIRED"}, 401)

        email = resolve_token(r, token)
        if not email:
            return JSONResponse({"detail": "AUTHENTICATION REQUIRED"}, 401)

        request.state.player_email = email
        return await call_next(request)
```

### 7. Updated Game endpoints (`src/main.py`)

Each endpoint signature changes from accepting `ActionRequest` (with `session_id`) to reading `player_email` from `request.state`:

```python
@app.post("/api/move")
def move_player(req: ActionRequest, request: Request):
    email = request.state.player_email   # injected by middleware
    game = get_game(email)
    game.move(req.direction)
    save_game(email, game)
    return game.to_state()
```

### 8. Login UI (`static/index.html` + `static/index.js` + `static/index.css`)

A new `#login-overlay` div is added to `index.html`. It is shown by default; the game canvas (`#game-container`) is hidden by default. JavaScript controls visibility transitions.

---

## Data Models

### Pydantic models (updated)

```python
# REMOVED: session_id from both models

class ActionRequest(BaseModel):
    direction: Optional[str] = None
    build_type: Optional[str] = None
    # session_id field removed — identity comes from Bearer token

class GameState(BaseModel):
    player: Player
    galaxy: Dict[str, Sector]
    log: str
    game_over: bool
    victory: bool
    # session_id field removed
    planets_owned: int = 0
    gas_clouds_owned: int = 0
    hunter_active: bool = False
    hunter_x: int = -1
    hunter_y: int = -1
    hunter_cooldown: int = 0
    ls_maintenance: int = 0
    difficulty: str = "EASY"
```

### New request/response models

```python
class OTPRequest(BaseModel):
    email: EmailStr       # Pydantic built-in RFC 5321 validation → auto-422 on bad input

class OTPVerifyRequest(BaseModel):
    email: EmailStr
    code: str             # 6-digit string; validated manually for exact format

class TokenResponse(BaseModel):
    token: str

class ValidateResponse(BaseModel):
    email: str
```

### Redis key reference

| Key pattern | Type | TTL | Contents |
|---|---|---|---|
| `otp:<email>` | String | 300 s | 6-digit OTP |
| `token:<uuid>` | String | 86 400 s | email address |
| `ratelimit:<email>` | String (counter) | 60 s | integer hit count |
| `session:<email>` | String (JSON) | 604 800 s | `GameState` JSON |

### Environment variables (updated `.env`)

```
REDIS_HOST=...
REDIS_PORT=...
REDIS_PASSWORD=...
REDIS_USER=default
BASE_PATH=
GMAIL_ADDRESS=...
GMAIL_APP_PASSWORD=...
```

The app raises a `RuntimeError` at startup if `GMAIL_ADDRESS` or `GMAIL_APP_PASSWORD` are absent.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: OTP format invariant

*For any* call to `generate_otp()`, the returned value must be a string of exactly 6 decimal digits (i.e. matching `^\d{6}$` and in the range `000000`–`999999`).

**Validates: Requirements 1.2**

---

### Property 2: OTP storage round-trip

*For any* valid email address, after calling the `request-otp` handler (with a mocked SMTP sender), the Redis key `otp:<email>` must exist, have a TTL in the range (0, 300], and its value must equal the OTP that was generated during that call.

**Validates: Requirements 1.4, 4.3**

---

### Property 3: Response never leaks OTP

*For any* valid email address, the HTTP 200 response body returned by `POST /api/auth/request-otp` must not contain the OTP value stored under `otp:<email>`.

**Validates: Requirements 1.6**

---

### Property 4: Invalid emails always rejected

*For any* string that does not satisfy RFC 5321 email format (empty string, missing `@`, multiple `@`, no domain, etc.), `POST /api/auth/request-otp` must return HTTP 422 and the mock SMTP sender must never be invoked.

**Validates: Requirements 1.5**

---

### Property 5: Successful OTP verification state transition

*For any* valid `(email, otp)` pair where `otp:<email>` is pre-populated in Redis with the matching OTP, calling `POST /api/auth/verify-otp` must:
- Delete `otp:<email>` (single-use enforcement),
- Create `token:<new_uuid>` mapping to `email` with TTL in (0, 86400],
- Return HTTP 200 with the new token in the response body.

**Validates: Requirements 2.3, 4.4**

---

### Property 6: OTP mismatch always returns 401

*For any* pair of distinct 6-digit strings `(stored, submitted)` where `stored ≠ submitted`, calling `POST /api/auth/verify-otp` with the `submitted` code must return HTTP 401 with message `"INVALID CODE"`.

**Validates: Requirements 2.4**

---

### Property 7: Re-login preserves game state

*For any* pre-existing `session:<email>` value in Redis, a successful `POST /api/auth/verify-otp` call must not modify, delete, or overwrite `session:<email>`. The game state must be identical before and after verification.

**Validates: Requirements 4.5**

---

### Property 8: Auth middleware blocks all unauthenticated game requests

*For any* game API endpoint under `/api/` (excluding `/api/auth/*`), a request without an `Authorization: Bearer <token>` header, or with a token not present in Redis, must receive HTTP 401 `"AUTHENTICATION REQUIRED"` regardless of the request body.

**Validates: Requirements 3.1, 3.3**

---

### Property 9: Auth middleware resolves correct game state key

*For any* `(token, email)` pair stored in Redis as `token:<token> → email`, a game action processed through the middleware must read and write game state under `session:<email>` — never under any other key pattern.

**Validates: Requirements 3.2, 4.1**

---

### Property 10: Game state write always sets 7-day TTL

*For any* call to `save_game(email, game)`, the Redis write must use TTL exactly 604 800 seconds, regardless of the email value, game state content, or which action triggered the save.

**Validates: Requirements 4.2**

---

### Property 11: Rate limiter blocks above threshold

*For any* email address, after exactly 3 accepted `POST /api/auth/request-otp` calls within a 60-second window, any further call within that window must return HTTP 429 `"TOO MANY REQUESTS. WAIT BEFORE RETRYING."` and the SMTP sender must not be invoked.

**Validates: Requirements 5.1, 5.2**

---

### Property 12: Token validation round-trip

*For any* valid `(token, email)` pair stored in Redis, `GET /api/auth/validate` with header `Authorization: Bearer <token>` must return HTTP 200 with the correct `email` in the response body.

**Validates: Requirements 8.2**

---

### Property 13: Email construction always embeds OTP

*For any* 6-digit OTP string, the email message constructed by `EmailSender` must have subject `"COSMIC CONQUEST — YOUR ACCESS CODE"` and a body that contains the OTP string verbatim.

**Validates: Requirements 7.3**

---

## Error Handling

| Scenario | HTTP status | Response body |
|---|---|---|
| Invalid email format | 422 | Pydantic validation detail |
| SMTP connection / auth failure | 503 | `"EMAIL DELIVERY FAILED. TRY AGAIN LATER."` |
| OTP mismatch | 401 | `"INVALID CODE"` |
| OTP expired / not found | 401 | `"CODE EXPIRED OR NOT FOUND"` |
| Rate limit exceeded | 429 | `"TOO MANY REQUESTS. WAIT BEFORE RETRYING."` |
| Missing / invalid Bearer token | 401 | `"AUTHENTICATION REQUIRED"` |
| Missing env vars at startup | `RuntimeError` | Logged to stderr, process exits |

All SMTP errors are caught, logged server-side with full traceback, and surfaced to the client only as the generic 503 message. OTP values are never included in log output at WARNING or above level.

### Frontend error handling

All error messages from the server are displayed inline in the Login UI below the relevant input field (`#email-error` or `#otp-error`). No page navigation occurs on error. On HTTP 401 during page-load token validation, `cc_token` is cleared from `localStorage` and the login screen is shown.

---

## Testing Strategy

### Unit tests (pytest + pytest-asyncio)

Focus on isolated pure-function correctness and specific error scenarios:

- `test_generate_otp`: verify format (covered also by property test).
- `test_verify_otp_expired`: missing Redis key → 401 "CODE EXPIRED OR NOT FOUND".
- `test_smtp_failure_returns_503`: mock raises `smtplib.SMTPException` → assert 503.
- `test_missing_env_vars_raises_at_startup`: unset `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` → `RuntimeError`.
- `test_action_request_has_no_session_id`: schema assertion on `ActionRequest`.
- `test_game_state_has_no_session_id`: schema assertion on `GameState`.
- `test_new_game_overwrites_existing_state`: pre-populate `session:<email>`, call `/api/new-game`, assert overwrite.

### Property-based tests (pytest + Hypothesis)

Each property from the Correctness Properties section maps to one Hypothesis test. Minimum **100 examples** per test. The test file is `tests/test_otp_login_properties.py`.

| Test name | Property | Strategy |
|---|---|---|
| `test_otp_format` | Property 1 | Call `generate_otp()` 100+ times; assert regex match |
| `test_otp_storage_round_trip` | Property 2 | `st.emails()` — request-otp, check Redis |
| `test_response_never_leaks_otp` | Property 3 | `st.emails()` — compare response body to stored OTP |
| `test_invalid_emails_rejected` | Property 4 | `st.text()` filtered to non-email strings |
| `test_verify_otp_state_transition` | Property 5 | `st.emails()`, `st.from_regex(r'\d{6}')` |
| `test_otp_mismatch_returns_401` | Property 6 | `st.from_regex(r'\d{6}')` pairs where values differ |
| `test_relogin_preserves_game_state` | Property 7 | `st.emails()`, arbitrary JSON game state |
| `test_auth_middleware_blocks_unauthenticated` | Property 8 | `st.sampled_from(game_endpoints)`, random bodies |
| `test_middleware_resolves_correct_key` | Property 9 | `st.emails()`, `st.uuids()` |
| `test_save_game_sets_7day_ttl` | Property 10 | `st.emails()`, all game action types |
| `test_rate_limiter_blocks_above_threshold` | Property 11 | `st.emails()` |
| `test_token_validate_round_trip` | Property 12 | `st.emails()`, `st.uuids()` |
| `test_email_construction_embeds_otp` | Property 13 | `st.from_regex(r'\d{6}')` |

All Redis calls in property tests use a `fakeredis.FakeRedis` instance. All SMTP calls use `unittest.mock.patch`.

**Tag format** used in each property test docstring:
`Feature: otp-login, Property <N>: <property_text>`

### Integration tests

- `test_smtp_connection`: with real (or staging) Gmail credentials, verify STARTTLS handshake and login succeed.
- `test_email_delivered`: end-to-end smoke — send to a test inbox and assert delivery (manual or mailhog).

### Test infrastructure additions

Add to `requirements.txt`:

```
pytest==8.2.0
pytest-asyncio==0.23.6
httpx==0.27.0          # async test client for FastAPI
hypothesis==6.100.0    # property-based testing
fakeredis==2.21.0      # in-memory Redis for tests
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: OTP Format Invariant

*For any* call to the OTP generation function, the result SHALL be a string of exactly 6 decimal digits (matching `/^\d{6}$/`).

**Validates: Requirements 1.2**

---

### Property 2: OTP Storage Round-Trip

*For any* valid email address, after a successful `POST /api/auth/request-otp`, the Redis key `otp:<email>` SHALL exist with a value equal to the generated OTP and a TTL that is positive and at most 300 seconds.

**Validates: Requirements 1.4**

---

### Property 3: Invalid Email Always Rejected

*For any* string that does not conform to RFC 5321 email format (missing `@`, missing domain, whitespace-only, empty string, numeric-only), `POST /api/auth/request-otp` SHALL return HTTP 422 and SHALL NOT write any key to Redis.

**Validates: Requirements 1.5**

---

### Property 4: Response Never Reveals OTP

*For any* valid email, the HTTP 200 response body from `POST /api/auth/request-otp` SHALL NOT contain the OTP value stored in `otp:<email>` in Redis.

**Validates: Requirements 1.6**

---

### Property 5: Successful Verification Atomically Deletes OTP and Creates Session Token

*For any* email and the valid OTP stored for it in Redis, a `POST /api/auth/verify-otp` with the correct OTP SHALL: (a) delete the `otp:<email>` key, (b) create a `token:<uuid>` key in Redis with value equal to the email and TTL at most 86400 seconds, and (c) return HTTP 200 with a `token` field whose value matches the created Redis key suffix.

**Validates: Requirements 2.3**

---

### Property 6: Wrong OTP Returns Specific 401

*For any* email with a stored OTP and *any* different 6-digit string submitted as the OTP, `POST /api/auth/verify-otp` SHALL return HTTP 401 with body `{"detail": "INVALID CODE"}`, and the `otp:<email>` key SHALL remain in Redis unchanged.

**Validates: Requirements 2.4**

---

### Property 7: Missing OTP Returns Specific 401

*For any* email that has no entry at `otp:<email>` in Redis (never requested or expired), `POST /api/auth/verify-otp` SHALL return HTTP 401 with body `{"detail": "CODE EXPIRED OR NOT FOUND"}`.

**Validates: Requirements 2.5**

---

### Property 8: Auth Guard on All Protected Endpoints

*For any* protected endpoint (all `/api/` endpoints except `/api/auth/*`) and *any* request with a missing `Authorization` header or a token not present in Redis, the response SHALL be HTTP 401 with body `{"detail": "AUTHENTICATION REQUIRED"}`.

**Validates: Requirements 3.3, 8.3**

---

### Property 9: Game State Round-Trip via Email Key with 7-Day TTL

*For any* email address with a valid session token, saving a game state via any write endpoint (move, new_game, colonize, etc.) SHALL write the state to `session:<email>` in Redis with a TTL that is positive and at most 604800 seconds, and subsequently loading via any read endpoint SHALL return an equivalent game state.

**Validates: Requirements 3.2, 4.1, 4.2**

---

### Property 10: Re-Login Preserves Existing Game State

*For any* email that has an existing `session:<email>` key in Redis, completing a full OTP authentication flow (request-otp → verify-otp) SHALL issue a new Session_Token but SHALL NOT modify or delete the `session:<email>` key.

**Validates: Requirements 4.5**

---

### Property 11: Explicit New Game Overwrites Existing Game State

*For any* email with an existing `session:<email>` key, a `POST /api/new_game` with a valid token for that email SHALL overwrite `session:<email>` with a fresh game state and reset the 7-day TTL.

**Validates: Requirements 4.6**

---

### Property 12: Rate Limiter Blocks Exactly After 3 Requests

*For any* email, the first 3 `POST /api/auth/request-otp` calls within a 60-second window SHALL each return HTTP 200, the `ratelimit:<email>` counter in Redis SHALL equal the number of accepted requests, the TTL SHALL be at most 60 seconds, and the 4th call SHALL return HTTP 429 with body `{"detail": "TOO MANY REQUESTS. WAIT BEFORE RETRYING."}`.

**Validates: Requirements 5.2, 5.3**

---

### Property 13: Email Body Contains OTP and Correct Subject

*For any* 6-digit OTP string, the email message constructed by `Email_Sender.send_otp` SHALL have the subject `"COSMIC CONQUEST — YOUR ACCESS CODE"` and a plain-text body that contains the OTP string verbatim.

**Validates: Requirements 7.3**

---

## Error Handling

### Auth Layer Errors

| Scenario | HTTP Status | Response Body |
|---|---|---|
| Invalid email format | 422 | Pydantic validation detail |
| Rate limit exceeded | 429 | `{"detail": "TOO MANY REQUESTS. WAIT BEFORE RETRYING."}` |
| SMTP failure | 503 | `{"detail": "EMAIL DELIVERY FAILED. TRY AGAIN LATER."}` |
| OTP mismatch | 401 | `{"detail": "INVALID CODE"}` |
| OTP expired/absent | 401 | `{"detail": "CODE EXPIRED OR NOT FOUND"}` |
| Token missing/invalid | 401 | `{"detail": "AUTHENTICATION REQUIRED"}` |

### SMTP Error Handling

`Email_Sender.send_otp` is wrapped in a try/except for `smtplib.SMTPException` and `OSError`. On any failure the exception is logged server-side (via Python's `logging` module, which already writes to `app.log`) and an HTTP 503 is returned to the client. The OTP is not stored in Redis when SMTP fails — the `/api/auth/request-otp` endpoint stores the OTP only after `send_otp` returns without raising.

### Redis Connectivity

Redis errors during auth operations propagate as HTTP 500. The existing Redis client (`r`) is already used throughout `main.py`; no additional error handling pattern is introduced.

### Startup Validation

```python
@app.on_event("startup")
async def validate_env():
    missing = [v for v in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD") if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {missing}")
```

### Frontend Error Handling

Errors are displayed inline:
- Below the email input: rate limit (429), validation (422), SMTP (503).
- Below the OTP input: invalid code (401 INVALID CODE), expired (401 CODE EXPIRED OR NOT FOUND).
- On stored-token validation failure (401): clear `cc_token`, show login UI from the beginning.
- On unexpected server errors (500): display a generic "SERVER ERROR. TRY AGAIN LATER." message.

No page navigation occurs on any error.

## Testing Strategy

### Dual Testing Approach

Both unit/example tests and property-based tests are used.

- **Unit tests** cover specific examples, integration points (SMTP mock, Redis mock), edge cases, and UI behavior.
- **Property tests** validate universal correctness properties (Properties 1–13 above) across many generated inputs.

### Property-Based Testing Library

**`hypothesis`** (Python) is the chosen PBT library. It integrates naturally with `pytest` and provides powerful strategies for generating emails, strings, and integers.

Add to `requirements.txt`:
```
pytest==8.1.0
pytest-asyncio==0.23.6
httpx==0.27.0
hypothesis==6.100.0
pydantic[email]==2.6.4
```

Each property test runs a minimum of **100 iterations** (Hypothesis default; can be overridden with `@settings(max_examples=100)`).

Each property test is tagged with a comment referencing the design property:
```python
# Feature: otp-login, Property 1: OTP Format Invariant
```

### Property Test Examples

```python
from hypothesis import given, settings, strategies as st

# Feature: otp-login, Property 1: OTP Format Invariant
@given(st.nothing())  # no input needed — generate_otp() takes no args
@settings(max_examples=200)
def test_otp_format_invariant():
    otp = generate_otp()
    assert re.fullmatch(r'\d{6}', otp)

# Feature: otp-login, Property 3: Invalid Email Always Rejected
@given(st.one_of(
    st.just(""),
    st.just("   "),
    st.text(min_size=1).filter(lambda s: '@' not in s),
    st.text(min_size=1).filter(lambda s: s.count('@') > 1),
))
@settings(max_examples=100)
def test_invalid_email_rejected(client, invalid_email):
    response = client.post("/api/auth/request-otp", json={"email": invalid_email})
    assert response.status_code == 422
```

### Unit Test Coverage

- `Email_Sender`: mock `smtplib.SMTP`, verify STARTTLS, login credentials, subject, body.
- `get_current_email` dependency: missing header, unknown token, valid token.
- `/api/auth/validate`: valid token → 200 + email, invalid → 401.
- Login UI flow: steps 1 and 2, error display, localStorage management, token validation on load.
- Startup validation: missing `GMAIL_ADDRESS` or `GMAIL_APP_PASSWORD` raises `RuntimeError`.

### Integration Tests

- Full OTP flow end-to-end with a real Redis instance (or `fakeredis`).
- SMTP integration tested with mocks only — no real email sent in CI.
