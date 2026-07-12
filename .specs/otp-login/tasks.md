# Implementation Plan: OTP Login

## Overview

Replace the anonymous UUID session system with email-based OTP authentication. The work breaks into five logical groups: (1) project plumbing and shared utilities, (2) the auth backend module, (3) middleware and game endpoint migration, (4) the frontend login UI, and (5) the test suite. Each group builds on the previous so there is no orphaned code.

## Tasks

- [x] 1. Add dependencies and environment validation
  - Add `pytest==8.1.0`, `pytest-asyncio==0.23.6`, `httpx==0.27.0`, `hypothesis==6.100.0`, `fakeredis==2.21.0`, `pydantic[email]==2.6.4` to `requirements.txt`
  - Add `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` entries (commented placeholders) to `.env`
  - Add a startup `validate_env()` hook in `src/main.py` that raises `RuntimeError` if either GMAIL variable is absent
  - _Requirements: 7.5_

- [x] 2. Implement OTP utilities and Email Sender
  - [x] 2.1 Create `src/auth_utils.py` with OTPService and RateLimiter pure functions
    - `generate_otp() -> str` — `secrets.randbelow(1_000_000)` zero-padded to 6 digits
    - `store_otp(r, email, otp)` — sets `otp:<email>` with TTL 300
    - `verify_and_consume_otp(r, email, submitted) -> bool` — atomic read-compare-delete
    - `check_and_increment(r, email, limit=3, window=60) -> bool` — sliding window counter
    - `issue_token(r, email) -> str` — UUID, writes `token:<uuid>` → email, TTL 86400
    - `resolve_token(r, token) -> str | None` — reads `token:<token>` or returns None
    - _Requirements: 1.2, 1.4, 2.3, 4.3, 4.4, 5.1, 5.3_

  - [x] 2.2 Write property test for OTP format invariant
    - **Property 1: OTP Format Invariant**
    - **Validates: Requirements 1.2**
    - File: `tests/test_otp_login_properties.py`
    - Strategy: call `generate_otp()` 200 times; assert `re.fullmatch(r'\d{6}', otp)`

  - [x] 2.3 Write property test for rate limiter threshold
    - **Property 12: Rate Limiter Blocks Exactly After 3 Requests**
    - **Validates: Requirements 5.2, 5.3**
    - Strategy: `st.emails()`, call `check_and_increment` 4 times on `fakeredis.FakeRedis`, assert first 3 return True and 4th returns False

  - [x] 2.4 Create `src/email_sender.py` with `send_otp_email(to_address, otp)`
    - Connect to `smtp.gmail.com:587` via STARTTLS
    - Authenticate with `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` from env
    - Send plain-text email with subject `COSMIC CONQUEST — YOUR ACCESS CODE`
    - Raise `smtplib.SMTPException` on failure (caller maps to HTTP 503)
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [x] 2.5 Write property test for email body construction
    - **Property 13: Email Body Contains OTP and Correct Subject**
    - **Validates: Requirements 7.3**
    - Strategy: `st.from_regex(r'\d{6}')`, mock `smtplib.SMTP`, assert subject and OTP verbatim in body

- [x] 3. Implement Auth Router (`src/auth.py`)
  - [x] 3.1 Create `src/auth.py` with Pydantic request/response models and router skeleton
    - `OTPRequest(email: EmailStr)`, `OTPVerifyRequest(email: EmailStr, code: str)`, `TokenResponse(token: str)`, `ValidateResponse(email: str)`
    - `router = APIRouter(prefix="/api/auth")`
    - _Requirements: 1.1, 2.1, 8.1_

  - [x] 3.2 Implement `POST /api/auth/request-otp` endpoint
    - Call `check_and_increment`; return 429 if over limit
    - Call `generate_otp` and `send_otp_email`; on SMTP exception return 503 (log traceback), do NOT store OTP
    - On success call `store_otp`, return HTTP 200 `{"message": "CODE SENT"}`
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 5.1, 5.2, 7.4_

  - [x] 3.3 Write property test: response never leaks OTP
    - **Property 4: Response Never Reveals OTP**
    - **Validates: Requirements 1.6**
    - Strategy: `st.emails()`, mock SMTP, assert stored `otp:<email>` value not in response body

  - [x] 3.4 Write property test: invalid emails always rejected
    - **Property 3: Invalid Email Always Rejected**
    - **Validates: Requirements 1.5**
    - Strategy: `st.one_of(st.just(""), st.text().filter(lambda s: '@' not in s), ...)`, assert 422 and no Redis writes

  - [x] 3.5 Write property test: OTP storage round-trip
    - **Property 2: OTP Storage Round-Trip**
    - **Validates: Requirements 1.4**
    - Strategy: `st.emails()`, call request-otp with mocked SMTP, verify `otp:<email>` in fakeredis with TTL ∈ (0, 300]

  - [x] 3.6 Implement `POST /api/auth/verify-otp` endpoint
    - Look up `otp:<email>`; if missing → 401 "CODE EXPIRED OR NOT FOUND"
    - If mismatch → 401 "INVALID CODE"
    - On match: delete `otp:<email>`, call `issue_token`, return `TokenResponse`
    - _Requirements: 2.2, 2.3, 2.4, 2.5_

  - [x] 3.7 Write property test: successful verification state transition
    - **Property 5: Successful Verification Atomically Deletes OTP and Creates Session Token**
    - **Validates: Requirements 2.3**
    - Strategy: `st.emails()`, `st.from_regex(r'\d{6}')`, pre-populate fakeredis, assert OTP deleted and `token:<uuid>` created with TTL ∈ (0, 86400]

  - [x] 3.8 Write property test: OTP mismatch returns 401
    - **Property 6: Wrong OTP Returns Specific 401**
    - **Validates: Requirements 2.4**
    - Strategy: distinct `(stored, submitted)` 6-digit pairs, assert 401 `"INVALID CODE"` and OTP key still present

  - [x] 3.9 Write property test: re-login preserves game state
    - **Property 10: Re-Login Preserves Existing Game State**
    - **Validates: Requirements 4.5**
    - Strategy: `st.emails()`, pre-populate `session:<email>` in fakeredis, run full OTP flow, assert session key unchanged

  - [x] 3.10 Implement `GET /api/auth/validate` endpoint
    - Extract Bearer token from `Authorization` header; if missing → 401
    - Call `resolve_token`; if None → 401; else return `ValidateResponse(email=email)`
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 3.11 Write property test: token validation round-trip
    - **Property 12: Token Validation Round-Trip**
    - **Validates: Requirements 8.2**
    - Strategy: `st.emails()`, `st.uuids()`, pre-populate fakeredis, assert GET /validate returns correct email

- [x] 4. Implement BearerTokenMiddleware and mount auth router
  - [x] 4.1 Create `BearerTokenMiddleware` in `src/auth.py`
    - Skip paths starting with `/api/auth` or not starting with `/api`
    - Extract Bearer token; missing or unknown → `JSONResponse({"detail": "AUTHENTICATION REQUIRED"}, 401)`
    - Inject `request.state.player_email` on success
    - _Requirements: 3.1, 3.3_

  - [x] 4.2 Register middleware and auth router in `src/main.py`
    - `app.add_middleware(BearerTokenMiddleware)` (before `PrefixMiddleware`)
    - `app.include_router(auth.router)`
    - Import `r` (Redis client) into `auth.py` or pass it as a dependency
    - _Requirements: 3.1, 3.3_

  - [x] 4.3 Write property test: auth middleware blocks unauthenticated requests
    - **Property 8: Auth Guard on All Protected Endpoints**
    - **Validates: Requirements 3.3, 8.3**
    - Strategy: `st.sampled_from(game_endpoints)`, no/invalid Authorization header, assert 401 `"AUTHENTICATION REQUIRED"`

- [x] 5. Migrate game endpoints to email-based identity
  - [x] 5.1 Update `ActionRequest` and `GameState` Pydantic models in `src/main.py`
    - Remove `session_id: str` from both models
    - _Requirements: 3.5_

  - [x] 5.2 Refactor `get_game` and `save_game` helpers
    - Change signature from `session_id: str` to `email: str`
    - Update Redis key from `session:<uuid>` to `session:<email>`
    - Update `save_game` TTL from 86400 to 604800 seconds (7 days)
    - Update `to_state()` call signature (remove `session_id` argument)
    - _Requirements: 3.2, 4.1, 4.2_

  - [x] 5.3 Update all game endpoints to use `request.state.player_email`
    - Add `request: Request` parameter to every endpoint
    - Replace `req.session_id` with `request.state.player_email` in all calls to `get_game` / `save_game`
    - Update `POST /api/new_game`: generate email-keyed state; signature no longer returns `session_id`
    - _Requirements: 3.1, 3.2, 4.1_

  - [x] 5.4 Write property test: game state stored under `session:<email>` with 7-day TTL
    - **Property 9: Game State Round-Trip via Email Key with 7-Day TTL**
    - **Validates: Requirements 3.2, 4.1, 4.2**
    - Strategy: `st.emails()` + all write action types, assert Redis key pattern and TTL ≤ 604800

  - [x] 5.5 Write property test: middleware resolves correct game state key
    - **Property 9 (key resolution aspect)**
    - **Validates: Requirements 3.2**
    - Strategy: `st.emails()`, `st.uuids()`, assert game actions read/write `session:<email>` not any other key

- [x] 6. Checkpoint — ensure all backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Update Login UI (frontend)
  - [x] 7.1 Add `#login-overlay` HTML to `static/index.html` and hide `#game-container` by default
    - Add `#login-overlay` div with email step (`#email-step`) and OTP step (`#otp-step`)
    - Include `#email-input`, `#send-code-btn`, `#email-error` in email step
    - Include `#otp-input`, `#verify-btn`, `#otp-error` in OTP step
    - Set `#game-container` to `display: none` by default
    - _Requirements: 1.1, 2.1, 6.1_

  - [x] 7.2 Style `#login-overlay` in `static/index.css`
    - Match CRT retro aesthetic: green-on-black, uppercase text, monospace font
    - Style input fields, buttons, and error message elements
    - _Requirements: 6.2_

  - [x] 7.3 Implement login flow logic in `static/index.js`
    - On page load: check `localStorage.getItem('cc_token')`; if present call `GET /api/auth/validate`
      - On 200: hide overlay, show game container, load initial game state
      - On 401: clear `cc_token`, show login overlay
    - "SEND CODE" button: `POST /api/auth/request-otp`; on 200 show OTP step; on error display in `#email-error`
    - "VERIFY" button: `POST /api/auth/verify-otp`; on 200 store token in `localStorage`, hide overlay, load game; on error display in `#otp-error`
    - _Requirements: 2.6, 6.3, 6.4, 6.5, 6.6_

  - [x] 7.4 Update `sendAction` and `startNewGame` in `static/index.js`
    - Remove all `session_id` references from request bodies
    - Add `Authorization: Bearer <token>` header to every `/api/` fetch call using `localStorage.getItem('cc_token')`
    - Update `startNewGame` to not capture `state.session_id`
    - _Requirements: 3.4, 3.5_

- [x] 8. Write unit tests for specific scenarios
  - [x] 8.1 Create `tests/test_otp_login_unit.py` with targeted unit tests
    - `test_verify_otp_expired`: missing Redis key → 401 "CODE EXPIRED OR NOT FOUND"
    - `test_smtp_failure_returns_503`: mock `smtplib.SMTPException` → assert 503
    - `test_missing_env_vars_raises_at_startup`: unset GMAIL vars → `RuntimeError`
    - `test_action_request_has_no_session_id`: assert `ActionRequest` has no `session_id` field
    - `test_game_state_has_no_session_id`: assert `GameState` has no `session_id` field
    - `test_new_game_overwrites_existing_state`: pre-populate `session:<email>`, POST `/api/new_game`, assert overwrite
    - _Requirements: 1.5, 2.5, 4.6, 7.4, 7.5_

- [x] 9. Final checkpoint — ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- The `src/auth.py` module keeps all auth code separate; `src/main.py` game logic stays clean
- `fakeredis.FakeRedis` is used in all property tests — no real Redis required in CI
- All SMTP calls in tests use `unittest.mock.patch` — no real email sent
- Property tests use `@settings(max_examples=100)` minimum; Property 1 uses 200
- Each property test docstring is tagged: `# Feature: otp-login, Property N: <title>`
- The `session_id` removal (task 5.1) must happen before or alongside endpoint migration (5.3)
- `PrefixMiddleware` already strips `BASE_PATH` — `BearerTokenMiddleware` sits inside it and sees clean paths

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.4", "3.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.5", "3.2", "3.6", "3.10"] },
    { "id": 3, "tasks": ["3.3", "3.4", "3.5", "3.7", "3.8", "3.9", "3.11", "4.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "5.1"] },
    { "id": 5, "tasks": ["5.2", "5.3"] },
    { "id": 6, "tasks": ["5.4", "5.5", "7.1", "7.4"] },
    { "id": 7, "tasks": ["7.2", "7.3", "8.1"] }
  ]
}
```
