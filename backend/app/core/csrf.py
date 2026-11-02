import json
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_from_referer(referer: str) -> str | None:
    parts = urlsplit(referer)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


class CSRFOriginMiddleware:
    """Rejects state-changing requests to cookie-authenticated paths from other websites.

    Browsers send Origin (or at least Referer) on cross-site POST/PUT/PATCH/DELETE. If one
    is present it must be an allowed origin. Requests with neither (curl, scripts) carry no
    browser cookies by accident, so they are let through.
    """

    def __init__(
        self, app: ASGIApp, *, allowed_origins: list[str], protected_prefixes: tuple[str, ...]
    ):
        self.app = app
        self.allowed_origins = {origin.rstrip("/") for origin in allowed_origins}
        self.protected_prefixes = protected_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in UNSAFE_METHODS
            and scope["path"].startswith(self.protected_prefixes)
        ):
            headers = {
                key.decode("latin-1"): value.decode("latin-1") for key, value in scope["headers"]
            }
            origin = headers.get("origin")
            if origin is None and headers.get("referer"):
                origin = _origin_from_referer(headers["referer"])
            if origin is not None and origin.rstrip("/") not in self.allowed_origins:
                await self._reject(send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps({"detail": "Request blocked: origin not allowed"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
