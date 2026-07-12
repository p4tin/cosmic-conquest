# Requirements: OTP Auth Library

## Overview
Extract the existing email-based OTP (One-Time Password) authentication and session management system from the Cosmic Conquest project into a reusable, standalone Python library. This will allow other FastAPI projects to easily drop in the OTP login flow, rate limiting, and token management functionality.

## Scope
- Extract the core OTP logic, Redis interactions, and email sending capabilities.
- Extract the FastAPI routers and middleware associated with authentication.
- Package the extracted code as a standard, installable Python library in a new `packages/fastapi-otp-login` directory within this repository (which you can easily split into its own repo later).
- Refactor the current Cosmic Conquest application to consume this new library via a local `pip install -e packages/fastapi-otp-login`.

## Acceptance Criteria

1. **Extraction**
   - WHEN the library is installed in a new FastAPI project, THEN the project SHALL be able to mount the OTP authentication router and use the token middleware without copying source files.

2. **Configuration**
   - IF a consuming project provides Redis credentials and Gmail SMTP credentials via environment variables or a config object, THEN the library SHALL successfully connect to Redis and send OTP emails.

3. **Functionality Parity**
   - WHEN the library handles an OTP request, THEN it SHALL enforce the same rate limits (e.g., 3 requests per 60 seconds) and 5-minute OTP expiry as the original implementation.
   - WHEN a valid OTP is verified, THEN the library SHALL issue a 24-hour session token.

4. **Integration**
   - WHEN Cosmic Conquest is run after the refactor, THEN it SHALL use the extracted library for all authentication without any regression in the existing login flow or tests.
