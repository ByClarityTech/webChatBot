import time
from threading import Lock

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

_WINDOW_SECONDS = 3600


class InMemoryRateLimiter:
    """Sliding-window counter — single-process MVP (no Redis)."""

    def __init__(self, *, limit: int, window_seconds: int = _WINDOW_SECONDS) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            recent = [ts for ts in self._hits.get(key, []) if ts > cutoff]
            if len(recent) >= self._limit:
                self._hits[key] = recent
                return False
            recent.append(now)
            self._hits[key] = recent
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_booking_limiter: InMemoryRateLimiter | None = None


def _get_booking_limiter() -> InMemoryRateLimiter:
    global _booking_limiter
    if _booking_limiter is None:
        settings = get_settings()
        _booking_limiter = InMemoryRateLimiter(limit=settings.booking_rate_limit_per_hour)
    return _booking_limiter


def reset_booking_rate_limiter() -> None:
    """Clear limiter state — for tests only."""
    global _booking_limiter
    _booking_limiter = None


def booking_rate_limit_key(client_ip: str, thread_id: str | None) -> str:
    if thread_id:
        return f"{client_ip}:{thread_id}"
    return client_ip


def check_booking_rate_limit(client_ip: str, thread_id: str | None) -> bool:
    key = booking_rate_limit_key(client_ip, thread_id)
    return _get_booking_limiter().allow(key)


class FailedAttemptLimiter:
    """Counts failures only; a success clears the key. Single-process MVP (no Redis)."""

    def __init__(self, *, max_failures: int, window_seconds: int) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        cutoff = now - self._window_seconds
        recent = [ts for ts in self._failures.get(key, []) if ts > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def retry_after(self, key: str) -> int | None:
        """Seconds until the key may try again, or None if it is not locked."""
        now = time.monotonic()
        with self._lock:
            recent = self._recent(key, now)
            if len(recent) < self._max_failures:
                return None
            oldest_counted = recent[-self._max_failures]
            return max(1, int(oldest_counted + self._window_seconds - now) + 1)

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._failures[key] = [*self._recent(key, now), now]

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


_login_failure_limiter: FailedAttemptLimiter | None = None


def get_login_failure_limiter() -> FailedAttemptLimiter:
    global _login_failure_limiter
    if _login_failure_limiter is None:
        settings = get_settings()
        _login_failure_limiter = FailedAttemptLimiter(
            max_failures=settings.login_max_failed_attempts,
            window_seconds=settings.login_failure_window_minutes * 60,
        )
    return _login_failure_limiter


def reset_login_failure_limiter() -> None:
    """Clear limiter state — for tests only."""
    global _login_failure_limiter
    _login_failure_limiter = None


def login_failure_key(email: str, client_ip: str) -> str:
    return f"{email.strip().lower()}|{client_ip}"


_CHAT_WINDOW_SECONDS = 60
_CHAT_LIMIT_PER_WINDOW = 30
_chat_limiter: InMemoryRateLimiter | None = None


def _get_chat_limiter() -> InMemoryRateLimiter:
    global _chat_limiter
    if _chat_limiter is None:
        _chat_limiter = InMemoryRateLimiter(
            limit=_CHAT_LIMIT_PER_WINDOW, window_seconds=_CHAT_WINDOW_SECONDS
        )
    return _chat_limiter


def reset_chat_rate_limiter() -> None:
    """Clear limiter state — for tests only."""
    global _chat_limiter
    _chat_limiter = None


def chat_client_ip(request: Request) -> str:
    """The socket peer. X-Forwarded-For is used only when TRUST_X_FORWARDED_FOR is on, and then
    only its last entry: that is the one the single trusted proxy appended. Earlier entries
    come from the caller and can be anything."""
    if get_settings().trust_x_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            last = forwarded.split(",")[-1].strip()
            if last:
                return last
    return request.client.host if request.client else "unknown"


def check_chat_rate_limit(request: Request) -> None:
    """30 chat requests per minute per IP. Raises 429 with Retry-After when exceeded."""
    if not _get_chat_limiter().allow(chat_client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many messages. Please wait a moment before sending more.",
            headers={"Retry-After": str(_CHAT_WINDOW_SECONDS)},
        )
