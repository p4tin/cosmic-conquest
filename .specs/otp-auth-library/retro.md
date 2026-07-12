# Retrospective: OTP Auth Library

## What Went Right
- **Clean Interface Extraction**: The auth functionality was successfully extracted into a decoupled, reusable library (`fastapi-otp-login`) without breaking any core game logic.
- **Dependency Injection Integration**: Modifying endpoints and middleware to accept explicit `OTPAuthConfig` and Redis client instances eliminated reliance on global state, allowing for cleaner testing.
- **Test Integrity**: The 24-test integration test suite (including 13 Hypothesis properties) was successfully preserved and used to verify implementation parity of the new package.
- **Warning-Free Testing**: Updated and upgraded FastAPI, pytest, pytest-asyncio, and httpx (to httpx2) to fully eliminate all 4,844 deprecation warnings under Python 3.14.

## What Went Wrong
- **Python 3.14 Compatibility Issue**: The local virtual environment runs CPython 3.14.4. The pinned `pydantic-core==2.16.3` was incompatible with the new Python pre-release internals, causing compilation failure.
- **Third-Party Deprecation Warnings**: Older versions of `fastapi` and `starlette` used `asyncio.iscoroutinefunction`, which triggered thousands of deprecation warnings on Python 3.14.
- **Mismatched Mocks**: The properties tests previously hardcoded mocks matching internal modules (`src.email_sender.smtplib.SMTP`). When the files were deleted, these mocks broke.
- **Event Loop Semantics**: Python 3.14 strictness around `asyncio.get_event_loop()` caused a test failure when validate environment checks were executed in unit tests.

## Action Items
1. **Target Pre-Release Testing Early**: When testing code in pre-release Python versions, check build/compilation requirements and warning deprecations for core dependencies (like Pydantic/FastAPI) to avoid compiler errors and warnings.
2. **Abstract External Mock Targets**: Align test mocks directly with the library boundary or external service layers to avoid breakage when internal files are reorganized.
