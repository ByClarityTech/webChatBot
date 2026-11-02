import time
from threading import Lock

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
_admin_login_limiter: InMemoryRateLimiter | None = None


def _get_booking_limiter() -> InMemoryRateLimiter:
    global _booking_limiter
    if _booking_limiter is None:
        settings = get_settings()
        _booking_limiter = InMemoryRateLimiter(limit=settings.booking_rate_limit_per_hour)
    return _booking_limiter


def _get_admin_login_limiter() -> InMemoryRateLimiter:
    global _admin_login_limiter
    if _admin_login_limiter is None:
        settings = get_settings()
        _admin_login_limiter = InMemoryRateLimiter(
            limit=settings.admin_login_rate_limit_per_hour,
        )
    return _admin_login_limiter


def reset_booking_rate_limiter() -> None:
    """Clear limiter state — for tests only."""
    global _booking_limiter
    _booking_limiter = None


def reset_admin_login_rate_limiter() -> None:
    """Clear limiter state — for tests only."""
    global _admin_login_limiter
    _admin_login_limiter = None


def booking_rate_limit_key(client_ip: str, thread_id: str | None) -> str:
    if thread_id:
        return f"{client_ip}:{thread_id}"
    return client_ip


def check_booking_rate_limit(client_ip: str, thread_id: str | None) -> bool:
    key = booking_rate_limit_key(client_ip, thread_id)
    return _get_booking_limiter().allow(key)


def check_admin_login_rate_limit(client_ip: str) -> bool:
    return _get_admin_login_limiter().allow(client_ip)
