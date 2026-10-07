"""Which versions of the queue server we may run, and the notice that goes with them.

The job queue speaks the Redis protocol. Two servers do, with different licences:

* **Redis up to 7.2.x**: BSD-3-Clause. Free for commercial use; the copyright and licence notice must be kept when it is
  redistributed ("free" is not "no obligations").
* **Redis 7.4 and later** (7.4 changed it to RSALv2 / SSPLv1, 8.0 added AGPLv3 as a third choice): NOT allowed here. This
  also means a floating image tag such as ``redis:7-alpine`` is not allowed, because it moves to 7.4+. (7.3 is an unstable,
  never-for-production line; treated the same.)
* **Valkey**: BSD-3-Clause, the Linux Foundation fork of Redis 7.2.4. Allowed at any version.

The running server is asked who it is (``INFO server``); a version that is not allowed stops the service in production and
is a warning in dev. An unreachable server is not a licence question: the health page already says the queue is down.
Pure functions apart from ``require_allowed``; no network in ``verdict``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..logging import get_logger

log = get_logger(__name__)

# the highest Redis (major, minor) that is still BSD-3-Clause
REDIS_MAX = (7, 2)

NOTICE_LINES = [
    "- Redis 7.2 or older (job queue): BSD-3-Clause. Keep the copyright and licence notice that ships with the Redis package",
    "  (for the Debian / Ubuntu package: /usr/share/doc/redis-server/copyright) wherever Redis is redistributed.",
    "  Redis 7.4 and later are NOT used (RSALv2 / SSPLv1, and AGPLv3 from 8.0).",
    "- Valkey (alternative job queue): BSD-3-Clause. Keep its copyright and licence notice when it is redistributed.",
]


class ServerLicenceError(RuntimeError):
    """The queue server in use is a version this product may not run."""


@dataclass(frozen=True)
class Verdict:
    ok: bool
    name: str | None
    version: str | None
    licence: str | None
    reason: str


def parse(server: str | None) -> tuple[str | None, tuple[int, ...] | None]:
    """``'redis 7.0.15'`` -> ``('redis', (7, 0, 15))``; anything it cannot read -> ``(None, None)``."""
    m = re.match(r"^\s*(redis|valkey)\s+v?(\d+(?:\.\d+){0,3})", (server or "").casefold())
    if not m:
        return None, None
    return m.group(1), tuple(int(p) for p in m.group(2).split("."))


def verdict(server: str | None) -> Verdict:
    """May this server be used? ``server`` is what ``INFO server`` reported (``'redis 7.0.15'`` / ``'valkey 8.0.1'``)."""
    name, ver = parse(server)
    if name is None or ver is None:
        return Verdict(False, None, None, None, f"cannot tell which server this is ({server!r}): not accepted until it can be read")
    v = ".".join(str(p) for p in ver)
    if name == "valkey":
        return Verdict(True, name, v, "BSD-3-Clause", "Valkey is BSD-3-Clause")
    if (ver + (0, 0))[:2] <= REDIS_MAX:
        return Verdict(True, name, v, "BSD-3-Clause",
                       f"Redis {v} is BSD-3-Clause (7.2 or older): keep its copyright and licence notice when redistributing")
    return Verdict(False, name, v, "RSALv2 / SSPLv1 (AGPLv3 from 8.0)",
                   f"Redis {v} is not BSD-licensed (7.4 and later are RSALv2 / SSPLv1, 8.0 adds AGPLv3). "
                   f"Use Redis 7.2.x or Valkey; avoid floating tags such as redis:7-alpine")


def require_allowed(server: str | None = None) -> Verdict | None:
    """Start-up check. Outside dev a server that is not allowed stops the service; in dev it is logged. Returns the
    verdict, or ``None`` when the server cannot be reached (not a licence matter)."""
    from .. import swap
    from ..config import settings

    if not settings.server_licence_enforce:
        return None
    if server is None:
        try:
            server = swap.resolve("job_queue").server()          # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            server = None
    if server is None:
        return None
    v = verdict(server)
    if v.ok:
        log.info("queue_server_licence_ok", server=server, licence=v.licence)
        return v
    if settings.env != "dev":
        raise ServerLicenceError(f"queue server not allowed: {v.reason}")
    log.warning("queue_server_licence_not_allowed", server=server, reason=v.reason)
    return v
