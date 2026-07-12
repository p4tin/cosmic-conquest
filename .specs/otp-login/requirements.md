# Requirements Document

## Introduction

This feature replaces the anonymous UUID session-based identification in Cosmic Conquest with an email-based OTP (One-Time Password) authentication system. Instead of the game automatically creating a session UUID on load, players must first log in by providing their email address, receiving a 6-digit OTP via Gmail SMTP, and verifying it. After successful verification a signed session token is issued, stored in the browser's `localStorage`, and sent with every subsequent API request. The existing Redis key scheme (`session:<uuid>`) is replaced by `session:<email>` keys so game state is tied to a persistent identity across sessions. Game state has its own long TTL independent of the auth token, so a player can start a game today and resume it days later. A login UI is displayed before the game canvas loads, and OTP requests are rate-limited to prevent abuse.

## Glossary

- **Auth_Service**: The FastAPI authentication layer responsible for OTP generation, email delivery, and session token issuance.
- **Email_Sender**: The component that connects to Gmail SMTP using `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` and delivers OTP emails.
- **OTP**: A 6-digit numeric one-time password valid for 5 minutes and single-use.
- **Session_Token**: An opaque UUID token mapped to Player_Email in Redis, issued after successful OTP verification. Stored in browser `localStorage` and sent in the `Authorization: Bearer <token>` header on every game API request. Valid for 24 hours.
- **Login_UI**: The HTML/CSS/JS login screen rendered before the game canvas. Includes an email entry step and an OTP entry step.
- **Rate_Limiter**: Server-side logic that restricts how many OTP requests a single email address can make within a time window.
- **Game_Service**: The existing FastAPI game logic (move, fight, colonize, etc.) now protected behind session token authentication.
- **Player_Email**: The email address used as the canonical player identity and as the Redis key prefix for game state.
- **Redis_Auth_Store**: Redis keys used exclusively for OTP and session token storage, separate in naming from game state keys.

---

## Requirements

### Requirement 1: Email-Based OTP Request

**User Story:** As a player, I want to enter my email address to receive a one-time password, so that I can log in without a username/password account.

#### Acceptance Criteria

1. THE Login_UI SHALL display an email input field and a "SEND CODE" button before the game canvas is rendered.
2. WHEN a player submits a valid email address, THE Auth_Service SHALL generate a cryptographically random 6-digit numeric OTP.
3. WHEN an OTP is generated, THE Email_Sender SHALL send the OTP to the submitted email address using Gmail SMTP configured with `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` from the environment.
4. WHEN an OTP is successfully sent, THE Auth_Service SHALL store the OTP in Redis under the key `otp:<email>` with a TTL of 300 seconds (5 minutes).
5. IF the submitted email address does not conform to standard email format (RFC 5321), THEN THE Auth_Service SHALL return HTTP 422 with a descriptive validation error and SHALL NOT send an email.
6. WHEN an OTP is successfully sent, THE Auth_Service SHALL return HTTP 200 with a confirmation message that does not reveal the OTP value.

---

### Requirement 2: OTP Verification and Session Token Issuance

**User Story:** As a player, I want to enter the OTP I received to complete login and receive a session token, so that the game can identify me on subsequent requests.

#### Acceptance Criteria

1. THE Login_UI SHALL display a 6-digit OTP input field and a "VERIFY" button after the player submits a valid email.
2. WHEN a player submits an email and OTP pair, THE Auth_Service SHALL look up the stored OTP at `otp:<email>` in Redis.
3. IF the submitted OTP matches the stored value and has not expired, THEN THE Auth_Service SHALL delete the `otp:<email>` key, generate a Session_Token (UUID), store it at `token:<session_token>` mapped to the Player_Email with a TTL of 86400 seconds (24 hours), and return the Session_Token in the HTTP 200 response body.
4. IF the submitted OTP does not match the stored value, THEN THE Auth_Service SHALL return HTTP 401 with the message "INVALID CODE".
5. IF no OTP exists at `otp:<email>` (expired or never requested), THEN THE Auth_Service SHALL return HTTP 401 with the message "CODE EXPIRED OR NOT FOUND".
6. WHEN a Session_Token is issued, THE Login_UI SHALL store the Session_Token in `localStorage` under the key `cc_token` and SHALL hide the Login_UI and render the game canvas.

---

### Requirement 3: Authenticated Game API Requests

**User Story:** As a player, I want my game session to be linked to my email identity, so that my game state persists across browser sessions.

#### Acceptance Criteria

1. WHEN a game API request is received (any endpoint under `/api/`), THE Game_Service SHALL extract the Session_Token from the `Authorization: Bearer <token>` HTTP header.
2. WHEN a valid Session_Token is present, THE Game_Service SHALL resolve the Player_Email from `token:<session_token>` in Redis and use the Player_Email as the Redis game state key (`session:<email>`).
3. IF the `Authorization` header is absent or the Session_Token is not found in Redis, THEN THE Game_Service SHALL return HTTP 401 with the message "AUTHENTICATION REQUIRED".
4. THE Frontend SHALL attach the `Authorization: Bearer <token>` header to every `/api/` fetch request using the Session_Token stored in `localStorage`.
5. THE `session_id` field SHALL be removed from `ActionRequest` and `GameState` Pydantic models; the Player_Email derived from the token SHALL serve as the session identifier in all Redis operations.

---

### Requirement 4: Redis Key Scheme and Game State Persistence

**User Story:** As a player, I want my game to be saved under my email address with a long enough lifetime that I can start a game today and finish it tomorrow (or later), so that I never lose progress between logins.

#### Acceptance Criteria

1. THE Game_Service SHALL store and retrieve game state using the Redis key `session:<email>` where `<email>` is the authenticated Player_Email.
2. THE Game_Service SHALL set (or refresh) the TTL on `session:<email>` to 604800 seconds (7 days) every time the game state is written to Redis, ensuring an active game never expires mid-campaign.
3. THE Game_Service SHALL store and retrieve OTP values using the Redis key `otp:<email>` with a TTL of 300 seconds (5 minutes).
4. THE Game_Service SHALL store and retrieve session tokens using the Redis key `token:<session_token>` with a TTL of 86400 seconds (24 hours).
5. WHEN a player logs in again after their previous Session_Token has expired, THE Game_Service SHALL issue a new Session_Token but SHALL NOT delete or reset the existing `session:<email>` game state, so the player resumes their saved game.
6. WHEN a new game is explicitly started (e.g. `/api/new-game`) for an email that already has saved game state, THE Game_Service SHALL overwrite the existing `session:<email>` key with the new game state and reset the 7-day TTL.

---

### Requirement 5: OTP Rate Limiting

**User Story:** As a system operator, I want OTP requests to be rate-limited per email address, so that the Gmail account is not spammed and the service is not abused.

#### Acceptance Criteria

1. THE Auth_Service SHALL track the number of OTP requests per email address within a 60-second sliding window using a Redis key `ratelimit:<email>`.
2. IF a player requests more than 3 OTPs for the same email address within any 60-second window, THEN THE Auth_Service SHALL return HTTP 429 with the message "TOO MANY REQUESTS. WAIT BEFORE RETRYING." and SHALL NOT generate or send an OTP.
3. WHEN an OTP request is accepted (not rate-limited), THE Auth_Service SHALL increment the `ratelimit:<email>` counter in Redis and set its TTL to 60 seconds if no TTL is currently set.
4. THE Login_UI SHALL display the "TOO MANY REQUESTS" error message to the player when HTTP 429 is received.

---

### Requirement 6: Login UI Presentation and Flow

**User Story:** As a player, I want a clear login screen before the game loads, so that I know I need to authenticate before playing.

#### Acceptance Criteria

1. THE Login_UI SHALL be rendered in the browser before the game canvas (`game-container`) is made visible.
2. THE Login_UI SHALL maintain the existing CRT retro aesthetic (green-on-black, uppercase text, monospace font) consistent with the rest of the game.
3. WHEN the player loads the page and no valid Session_Token exists in `localStorage`, THE Login_UI SHALL be shown and the game canvas SHALL be hidden.
4. WHEN the player loads the page and a valid Session_Token exists in `localStorage`, THE Frontend SHALL skip the Login_UI, validate the token with the server, and render the game canvas directly.
5. IF the server returns HTTP 401 when validating a stored Session_Token on page load, THEN THE Frontend SHALL clear the stored `cc_token` from `localStorage` and display the Login_UI.
6. THE Login_UI SHALL display server-returned error messages (invalid code, expired code, rate limit) inline below the relevant input field without navigating away from the page.

---

### Requirement 7: Gmail SMTP Email Delivery

**User Story:** As a system operator, I want OTP emails sent via my personal Gmail account using an App Password, so that I can use existing email infrastructure without a third-party service.

#### Acceptance Criteria

1. THE Email_Sender SHALL connect to Gmail SMTP at host `smtp.gmail.com` on port 587 using STARTTLS.
2. THE Email_Sender SHALL authenticate with the Gmail SMTP server using the `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` values loaded from the environment via `python-dotenv`.
3. THE Email_Sender SHALL send emails with the subject "COSMIC CONQUEST — YOUR ACCESS CODE" and a plain-text body containing the OTP value.
4. IF the SMTP connection or authentication fails, THEN THE Auth_Service SHALL return HTTP 503 with the message "EMAIL DELIVERY FAILED. TRY AGAIN LATER." and SHALL log the error details server-side.
5. THE `.env` file SHALL include the variables `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`, and THE Auth_Service SHALL raise a startup error if either variable is absent.

---

### Requirement 8: Session Token Validation Endpoint

**User Story:** As a developer, I want a lightweight endpoint to verify a stored session token, so that the frontend can confirm token validity on page load without triggering a full game state load.

#### Acceptance Criteria

1. THE Auth_Service SHALL expose a `GET /api/auth/validate` endpoint that accepts the `Authorization: Bearer <token>` header.
2. WHEN a valid, non-expired Session_Token is provided, THE Auth_Service SHALL return HTTP 200 with the Player_Email associated with the token.
3. IF the Session_Token is missing, invalid, or expired, THE Auth_Service SHALL return HTTP 401.
