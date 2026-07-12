# Tasks: OTP Auth Library

1. **Initialize Package Structure**
   - Create `packages/fastapi-otp-login/src/fastapi_otp_login/` directories.
   - Create a `pyproject.toml` configuring the build system and dependencies (`fastapi`, `pydantic[email]`, `redis`).
   - Create a basic `README.md`.

2. **Implement Data Models and Config**
   - Create `config.py` with the `OTPAuthConfig` model (fields for `smtp_username`, `smtp_password`, `smtp_server`, `smtp_port`, `sender_email`).
   - Create `models.py` and migrate the `OTPRequest`, `OTPVerify`, and `TokenResponse` models from the existing `auth.py`.

3. **Migrate Core Utilities**
   - Create `utils.py`.
   - Copy all functions from the existing `src/auth_utils.py` into this file.
   - Verify they remain stateless (accepting the Redis client as an argument).

4. **Migrate Email Sender**
   - Create `email.py`.
   - Copy the email sending logic from `src/email_sender.py`.
   - Refactor it to accept an `OTPAuthConfig` instance rather than reading `os.environ` directly.

5. **Migrate FastAPI Router**
   - Create `router.py`.
   - Port the endpoints from `src/auth.py`.
   - Wrap them in a `get_auth_router(redis_client, config: OTPAuthConfig) -> APIRouter` factory function to inject the dependencies into the route handlers.

6. **Migrate Middleware**
   - Create `middleware.py`.
   - Port the `BearerTokenMiddleware` from `src/auth.py`.
   - Update its `__init__` to accept the `redis_client` explicitly, rather than relying on global state.

7. **Refactor Cosmic Conquest Application**
   - Add `-e packages/fastapi-otp-login` to `requirements.txt`.
   - Update `src/main.py` to import from `fastapi_otp_login`.
   - Initialize `OTPAuthConfig` in `main.py` using the `.env` variables.
   - Mount the router using `get_auth_router` and attach the updated middleware.

8. **Cleanup and Verification**
   - Delete `src/auth.py`, `src/auth_utils.py`, and `src/email_sender.py`.
   - Run the existing test suite (`pytest tests/`) to ensure all property-based tests and unit tests still pass perfectly.
