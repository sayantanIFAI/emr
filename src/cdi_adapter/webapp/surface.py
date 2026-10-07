"""What the web app shows to whom (deployment surface).

Two rules, both applied in ONE place so no route can be forgotten:

1. **Sign-in.** When ``CDI_ADMIN_PASSWORD`` is set, every request except ``/healthz`` needs HTTP Basic
   credentials (constant-time compare; a wrong attempt is slowed down). Any environment other than
   ``dev`` refuses to start without a password (:func:`require_auth_configured`).
2. **Closed paths.** Unless the owner switches them on, the review / reviewer / correction / registry
   paths (``CDI_REVIEW_UI_ENABLED``) and every FHIR path (``CDI_FHIR_ENABLED``) answer 404 as if they
   did not exist. The routes stay registered, so switching a flag on needs no code change.

Pure ASGI, so a large upload is never buffered by the middleware.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import settings

_Scope = dict[str, Any]
_Receive = Callable[[], Awaitable[dict[str, Any]]]
_Send = Callable[[dict[str, Any]], Awaitable[None]]

OPEN_PATHS = ("/healthz",)

_REVIEW = re.compile(
    r"^/(review|reviewer|admin)(/|$)"
    r"|^/api/(reviewer|admin|review|facts|corrections|doctors|registry|patients)(/|$)"
    r"|^/api/jobs/[^/]+/(facts|generate)(/|$)"
    r"|^/api/documents/[^/]+/(evidence|field-records)(/|$)")
_FHIR = re.compile(
    r"^/api/jobs/[^/]+/fhir(/|$)"
    r"|^/api/documents/[^/]+/bundle(/|$)"
    r"|^/api/patients/[^/]+/fhir(/|$)")


def is_closed(path: str) -> bool:
    """True when ``path`` must answer 404 under the current settings."""
    if not settings.review_ui_enabled and _REVIEW.search(path):
        return True
    return not settings.fhir_enabled and bool(_FHIR.search(path))


def require_auth_configured() -> None:
    """Called before the server starts: only a ``dev`` environment may run without a password."""
    if not settings.admin_password and settings.env.strip().lower() != "dev":
        raise RuntimeError(
            "CDI_ADMIN_PASSWORD is not set. The web app refuses to start without a sign-in unless "
            "CDI_ENV=dev. Set CDI_ADMIN_PASSWORD (and optionally CDI_ADMIN_USER).")


def reviewer_name(request: Any, claimed: str | None) -> str:
    """Who is acting. When a sign-in is configured the name is the signed-in user and whatever the request body
    says is ignored (a caller cannot sign a decision as someone else); with no sign-in (dev only) the claimed name
    is used, defaulting to ``reviewer``."""
    user = getattr(getattr(request, "state", None), "user", None)
    return user or (claimed or "reviewer")


def startup_checks() -> None:
    """Everything that must be true before this app serves a request. Run from ``main()`` AND from the app's
    lifespan, so ``uvicorn cdi_adapter.webapp.app:app`` is checked exactly like ``python -m cdi_adapter.webapp``."""
    from .. import swap
    from ..compliance.models import require_registered
    from ..security import require_no_default_credentials

    require_auth_configured()
    require_no_default_credentials()
    swap.check_all()               # a wrong name or a missing library stops the service, naming the setting
    require_registered()
    from ..compliance import servers

    servers.require_allowed()      # Redis 7.4+ (RSALv2 / SSPLv1 / AGPLv3) is refused outside dev; 7.2 or older and Valkey are fine


def credentials_ok(header: str | None) -> bool:
    """Check an ``Authorization: Basic ...`` header against the configured admin."""
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        user, _, pw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    # both compared every time, so the time taken does not say which one was wrong
    ok_user = hmac.compare_digest(user.encode(), settings.admin_user.encode())
    ok_pw = hmac.compare_digest(pw.encode(), settings.admin_password.encode())
    return ok_user and ok_pw


async def _reply(send: _Send, status: int, body: dict[str, str], extra: list[tuple[bytes, bytes]] | None = None) -> None:
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode()),
                            (b"cache-control", b"no-store"), *(extra or [])]})
    await send({"type": "http.response.body", "body": data})


class SurfaceMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if settings.admin_password and path not in OPEN_PATHS:
            auth = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"authorization"), None)
            if not credentials_ok(auth):
                await asyncio.sleep(0.5)                  # slows guessing; a correct login is not delayed
                await _reply(send, 401, {"detail": "Sign in required."},
                             [(b"www-authenticate", b'Basic realm="CDI admin", charset="UTF-8"')])
                return
            scope.setdefault("state", {})["user"] = settings.admin_user   # the one signed-in identity
        if is_closed(path):
            await _reply(send, 404, {"detail": "Not Found"})
            return
        await self.app(scope, receive, send)
