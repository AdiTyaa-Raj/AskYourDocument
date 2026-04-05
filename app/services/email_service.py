from __future__ import annotations

import logging
import os
import time

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

logger = logging.getLogger(__name__)

BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "adi7yaraj@gmail.com")
BREVO_SENDER_NAME = os.getenv("BREVO_SENDER_NAME", "AskYourDocument")


class BrevoNotConfiguredError(Exception):
    pass


class BrevoSendError(Exception):
    pass


def _get_api_instance() -> sib_api_v3_sdk.TransactionalEmailsApi:
    if not BREVO_API_KEY:
        raise BrevoNotConfiguredError("BREVO_API_KEY is not set")
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = BREVO_API_KEY
    return sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))


def _check_delivery_error(api_instance: sib_api_v3_sdk.TransactionalEmailsApi, message_id: str) -> None:
    """Poll Brevo event report for this message_id to surface async delivery errors."""
    # Give Brevo time to process the event (async pipeline takes a few seconds)
    time.sleep(5)

    # Brevo's event API expects the message ID without angle brackets
    clean_id = message_id.strip("<>")

    try:
        result = api_instance.get_email_event_report(
            message_id=clean_id,
            limit=10,
            sort="desc",
        )
        events = result.events or []
        logger.info("Brevo event report for message_id=%s: %d event(s) found", clean_id, len(events))
        for event in events:
            logger.info("  event=%s reason=%s", getattr(event, "_event", None), getattr(event, "_reason", None))
            if getattr(event, "_event", None) == "error":
                reason = getattr(event, "_reason", None) or "unknown reason"
                raise BrevoSendError(f"Brevo rejected email: {reason}")
    except BrevoSendError:
        raise
    except ApiException as exc:
        # Event API call itself failed — log as error and surface it
        logger.error(
            "Brevo event report API error for message_id=%s: %s %s",
            clean_id, exc.status, exc.reason,
        )
        raise BrevoSendError(f"Could not verify delivery status: {exc.status} {exc.reason}") from exc
    except Exception:
        logger.warning(
            "Could not verify email delivery status for message_id=%s — event polling failed",
            clean_id,
            exc_info=True,
        )


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_content: str,
    text_content: str | None = None,
) -> str:
    """Send a transactional email via Brevo. Returns the Brevo message ID."""
    api_instance = _get_api_instance()

    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email, "name": to_name}],
        sender={"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )

    try:
        response = api_instance.send_transac_email(send_smtp_email)
        logger.info("Email accepted by Brevo for %s, messageId=%s", to_email, response.message_id)
    except ApiException as exc:
        logger.exception("Brevo API error sending email to %s", to_email)
        raise BrevoSendError(f"Brevo API error: {exc.status} {exc.reason}") from exc

    # Brevo is async — it returns a message_id immediately even if delivery will fail.
    # Check the event report to surface errors like unverified sender.
    _check_delivery_error(api_instance, response.message_id)

    return response.message_id
