# Design: OTP Auth Library

## Architecture Overview
The OTP authentication library will be packaged as a standard Python distribution (`fastapi-otp-login`) using `pyproject.toml`. It will expose a clean API for any FastAPI application to integrate email-based OTP login and token middleware.

To ensure reusability, the library will not rely on global environment variables directly. Instead, consuming applications will pass a configuration object or initialize the components with their specific dependencies (like a Redis client or SMTP credentials).

## Component Breakdown

1. **Configuration Model (`config.py`)**
   - A Pydantic model `OTPAuthConfig` to hold the application name (`app_name`), SMTP credentials, email sender address, and optionally TTLs for tokens and OTPs.

2. **Core Logic (`core.py` or `utils.py`)**
   - Extracts functions from `auth_utils.py`: `generate_otp`, `store_otp`, `verify_and_consume_otp`, `check_and_increment`, `issue_token`, `resolve_token`.
   - All functions will continue to accept a `redis` client as their first argument to remain stateless.

3. **Email Sender (`email.py`)**
   - Extracts `send_otp_email`. It will be refactored to accept SMTP credentials (or an `OTPAuthConfig` instance) rather than pulling directly from `os.environ`.

4. **FastAPI Router (`router.py`)**
   - Extracts the `/request-otp`, `/verify-otp`, and `/validate` endpoints.
   - Provides a factory function `get_auth_router(redis_client, config: OTPAuthConfig) -> APIRouter` to generate the router with injected dependencies.

5. **Middleware (`middleware.py`)**
   - Extracts `BearerTokenMiddleware`.
   - Refactored so it can be configured with the `redis_client` and the base paths it should ignore (e.g., `/api/auth/`).

6. **Pydantic Models (`models.py`)**
   - Extracts the data models: `OTPRequest`, `OTPVerify`, `TokenResponse`.

## Directory Structure

```text
packages/fastapi-otp-login/
├── pyproject.toml
├── README.md
└── src/
    └── fastapi_otp_login/
        ├── __init__.py
        ├── config.py
        ├── models.py
        ├── utils.py
        ├── email.py
        ├── middleware.py
        └── router.py
```

## Data Flow (Integration into Cosmic Conquest)
1. `main.py` will import `OTPAuthConfig`, `get_auth_router`, and `BearerTokenMiddleware` from `fastapi_otp_login`.
2. `main.py` will initialize the `OTPAuthConfig` using the existing `.env` variables (`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`).
3. It will generate the auth router via `get_auth_router(redis_client, auth_config)` and include it in the FastAPI app.
4. It will add `BearerTokenMiddleware` to the app, passing the `redis_client`.
5. The local `auth.py`, `auth_utils.py`, and `email_sender.py` files will be deleted from the `src/` directory.

## Testing Strategy
- The current `tests/test_otp_login_properties.py` and unit tests in Cosmic Conquest will remain as integration tests to ensure the application still functions perfectly after swapping the local files for the library.
