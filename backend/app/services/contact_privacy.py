"""Phone numbers shared in chat: normalization, encryption at rest, and a lookup hash.

The phone is stored Fernet-encrypted. A keyed HMAC of the normalized number is stored beside
it so rows can be grouped by phone (FR-30) without decrypting every row. Both keys come from
settings; storing a contact is refused until both are configured.
"""

import hashlib
import hmac
import re

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class ContactStorageNotConfigured(Exception):
    """PHONE_ENCRYPTION_KEY or PHONE_HASH_KEY is missing or invalid."""


class InvalidPhoneError(ValueError):
    """Not a US number that can be normalized."""


_NON_DIGITS = re.compile(r"\D")


def normalize_us_phone(raw: str) -> str:
    """'(312) 555-0187', '1-312-555-0187' and '+1 312 555 0187' all become '+13125550187'."""
    digits = _NON_DIGITS.sub("", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # NANP numbers cannot start with 0 or 1 in the area code or the exchange.
    if len(digits) != 10 or digits[0] in "01" or digits[3] in "01":
        raise InvalidPhoneError("Enter a 10-digit US phone number")
    return f"+1{digits}"


def _fernet() -> Fernet:
    key = get_settings().phone_encryption_key
    if not key:
        raise ContactStorageNotConfigured("PHONE_ENCRYPTION_KEY is not set")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ContactStorageNotConfigured("PHONE_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt_phone(e164: str) -> bytes:
    return _fernet().encrypt(e164.encode("utf-8"))


def decrypt_phone(token: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(token)).decode("utf-8")
    except InvalidToken as exc:
        raise ContactStorageNotConfigured("Stored phone cannot be decrypted with this key") from exc


def phone_hash(e164: str) -> str:
    """Deterministic, keyed, 64 hex characters. Equal numbers give equal hashes."""
    key = get_settings().phone_hash_key
    if not key:
        raise ContactStorageNotConfigured("PHONE_HASH_KEY is not set")
    return hmac.new(key.encode("utf-8"), e164.encode("utf-8"), hashlib.sha256).hexdigest()
