"""
Email delivery module for Cosmic Conquest OTP authentication.

Sends one-time password emails via Gmail SMTP using STARTTLS.
OTP values are never written to logs at WARNING level or above.
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_SUBJECT = "COSMIC CONQUEST \u2014 YOUR ACCESS CODE"


def send_otp_email(to_address: str, otp: str) -> None:
    """
    Connect to smtp.gmail.com:587 via STARTTLS, authenticate with
    GMAIL_ADDRESS / GMAIL_APP_PASSWORD from the environment, and send a
    plain-text email containing the OTP.

    Raises:
        smtplib.SMTPException: On any SMTP-level failure (auth, send, etc.).
        OSError: On network-level failures (DNS, connection refused, etc.).

    The caller is expected to catch these and return HTTP 503.
    OTP values are never included in log output at WARNING level or above.
    """
    gmail_address = os.getenv("GMAIL_ADDRESS", "").strip()
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()

    body = (
        "COMMANDER,\n\n"
        f"YOUR ACCESS CODE: {otp}\n\n"
        "THIS CODE IS VALID FOR 5 MINUTES AND IS SINGLE-USE.\n\n"
        "IF YOU DID NOT REQUEST THIS CODE, DISREGARD THIS MESSAGE.\n\n"
        "— COSMIC CONQUEST COMMAND"
    )

    msg = MIMEText(body, "plain")
    msg["Subject"] = EMAIL_SUBJECT
    msg["From"] = gmail_address
    msg["To"] = to_address

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(gmail_address, gmail_app_password)
            smtp.sendmail(gmail_address, [to_address], msg.as_string())
        logger.info("OTP email dispatched to %s", to_address)
    except (smtplib.SMTPException, OSError):
        # Log full traceback server-side but never expose the OTP value.
        logger.exception(
            "Failed to send OTP email to %s via %s:%d",
            to_address,
            SMTP_HOST,
            SMTP_PORT,
        )
        raise
