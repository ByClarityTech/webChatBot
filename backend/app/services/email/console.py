import logging

from app.services.email.base import mask_email

logger = logging.getLogger(__name__)


class ConsoleEmailSender:
    """Used when AWS keys are missing. Never sends mail.

    In development the body is logged, including single-use links, so a developer can click
    them. Anywhere else the body is withheld and the log says the message was not sent.
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
        log = logger.error if self._app_env == "production" else logger.warning
        log("Email not sent: SES is not configured. subject=%s", subject)
        return None
