from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmailMessage:
    to_email: str
    subject: str
    html_content: str
    text_content: str


class EmailDeliveryError(Exception):
    """A message could not be handed to the provider.

    `code` is a fixed word such as SES_THROTTLED. It never holds provider text, an address, or
    a link. `retryable` tells the outbox whether a later attempt may succeed.
    """

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class EmailSender(Protocol):
    def send_email(
        self, to_email: str, subject: str, html_content: str, text_content: str
    ) -> str | None:
        """Send one message. Returns the provider's message ID when there is one."""
        ...


def mask_email(address: str) -> str:
    """'jane.doe@clinic.com' -> 'j***@clinic.com'. For logs only."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"
