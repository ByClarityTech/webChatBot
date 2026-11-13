"""Account email. Sends through Amazon SES when AWS keys are set, otherwise logs them.

Routes call deliver() from BackgroundTasks, after the response has gone out. deliver()
never raises: a failed email is logged, and an admin can send the invite again.
"""

import logging

from app.core.config import get_settings
from app.services.email.base import EmailDeliveryError, EmailMessage, EmailSender
from app.services.email.console import ConsoleEmailSender
from app.services.email.ses import DEFAULT_MAX_ATTEMPTS, SesEmailSender

logger = logging.getLogger(__name__)


def _ses_configured() -> bool:
    settings = get_settings()
    return bool(settings.aws_access_key_id and settings.aws_secret_access_key)


def get_email_sender(max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> EmailSender:
    settings = get_settings()
    if _ses_configured():
        return SesEmailSender(
            access_key_id=settings.aws_access_key_id,
            secret_access_key=settings.aws_secret_access_key.get_secret_value(),
            region=settings.ses_region,
            from_email=settings.emails_from_email,
            from_name=settings.emails_from_name,
            configuration_set=settings.ses_configuration_set,
            max_attempts=max_attempts,
        )
    return ConsoleEmailSender(app_env=settings.app_env)


def log_email_configuration() -> None:
    """Called once at startup. Never fails startup over missing keys."""
    settings = get_settings()
    if _ses_configured():
        logger.info("Email: Amazon SES in %s", settings.ses_region)
    elif settings.app_env == "production":
        logger.error("Email: AWS keys are not set. Invite and reset emails will not be sent.")
    else:
        logger.warning(
            "Email: AWS keys are not set. Emails are logged instead of sent (development)."
        )


def deliver(message: EmailMessage) -> None:
    try:
        get_email_sender().send_email(
            message.to_email,
            message.subject,
            message.html_content,
            message.text_content,
            from_name=message.from_name,
        )
    except EmailDeliveryError as exc:
        logger.error("Email delivery failed: %s subject=%s", exc, message.subject)
    except Exception as exc:  # a background task must not take the worker down
        logger.error(
            "Email delivery crashed: error=%s subject=%s", type(exc).__name__, message.subject
        )
