import logging

from app.services.email.base import EmailDeliveryError, mask_email

logger = logging.getLogger(__name__)


class ConsoleEmailSender:
    """Used when AWS keys are missing. Never sends mail.

    In development the body is logged, including single-use links, so a developer can click
    them, and the send counts as done. Anywhere else nothing is logged but the fact, and the
    send fails without retry: a notification must not be marked sent when nothing went out.
    """

    def __init__(self, *, app_env: str) -> None:
        self._app_env = app_env

    def send_email(self, to_email: str, subject: str, html_content: str, text_content: str) -> None:
        if self._app_env == "development":
            logger.info(
                "[console email, not sent] to=%s subject=%s\n%s",
                mask_email(to_email),
                subject,
                text_content,
            )
            return None
        logger.error("Email not sent: SES is not configured. subject=%s", subject)
        raise EmailDeliveryError("SES_NOT_CONFIGURED", retryable=False)
