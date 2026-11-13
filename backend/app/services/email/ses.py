import logging
import time
from email.utils import formataddr
from functools import lru_cache

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.services.email.base import EmailDeliveryError

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
MAX_ATTEMPTS = DEFAULT_MAX_ATTEMPTS
BACKOFF_SECONDS = 1.0
# SES error codes worth another try. A rejected address or message is not retried.
THROTTLED_CODES = frozenset({"Throttling", "ThrottlingException"})
UNAVAILABLE_CODES = frozenset({"ServiceUnavailable", "InternalFailure"})


@lru_cache(maxsize=4)
def _ses_client(access_key_id: str, secret_access_key: str, region: str):
    return boto3.client(
        "ses",
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )


class SesEmailSender:
    """Sends through Amazon SES. Errors are logged by code and message ID only.

    Exceptions are raised without chaining: botocore's messages can echo the recipient
    address, and the traceback would then carry it into the logs.

    `max_attempts` controls only the in-process retries. Account emails use the default. The
    notification outbox uses 1, because it keeps its own backoff schedule across restarts.
    """

    def __init__(
        self,
        *,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        from_email: str,
        from_name: str,
        configuration_set: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        client=None,
    ) -> None:
        self._client = client or _ses_client(access_key_id, secret_access_key, region)
        self._from_email = from_email
        self._source = formataddr((from_name, from_email))
        self._configuration_set = configuration_set
        self._max_attempts = max_attempts

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str,
        from_name: str | None = None,
    ) -> str:
        source = formataddr((from_name, self._from_email)) if from_name else self._source
        kwargs = {
            "Source": source,
            "Destination": {"ToAddresses": [to_email]},
            "Message": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": html_content, "Charset": "UTF-8"},
                    "Text": {"Data": text_content, "Charset": "UTF-8"},
                },
            },
        }
        if self._configuration_set:
            kwargs["ConfigurationSetName"] = self._configuration_set

        for attempt in range(1, self._max_attempts + 1):
            last = attempt == self._max_attempts
            try:
                response = self._client.send_email(**kwargs)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "unknown")
                logger.warning("SES send failed: code=%s attempt=%d", code, attempt)
                if code in THROTTLED_CODES:
                    error = EmailDeliveryError("SES_THROTTLED", retryable=True)
                elif code in UNAVAILABLE_CODES:
                    error = EmailDeliveryError("SES_UNAVAILABLE", retryable=True)
                else:
                    raise EmailDeliveryError("SES_REJECTED") from None
                if last:
                    raise error from None
            except BotoCoreError as exc:
                logger.warning("SES unreachable: error=%s attempt=%d", type(exc).__name__, attempt)
                if last:
                    raise EmailDeliveryError("SES_UNREACHABLE", retryable=True) from None
            else:
                message_id = response.get("MessageId", "")
                logger.info("Email accepted by SES: ses_message_id=%s", message_id)
                return message_id
            time.sleep(BACKOFF_SECONDS * attempt)

        raise EmailDeliveryError("SES_UNREACHABLE", retryable=True)  # pragma: no cover
