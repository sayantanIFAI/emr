"""Optional Redis read cache for a doctor's lexicon.

The database is the source of truth. The cache only makes repeated reads fast, it is rebuilt from the
database on a miss, it is dropped whenever that doctor's lexicon changes, and a Redis outage is never an
error: every call falls through to the database.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)
PREFIX = "cdi:doctor_profile:"


class ProfileCache:
    def __init__(self, client: Any, ttl_s: int | None = None) -> None:
        self._r = client
        self._ttl = settings.profile_cache_ttl_s if ttl_s is None else ttl_s

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self._r.get(PREFIX + key)
            return json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001 - a cache miss, not an error
            log.warning("profile_cache_get_failed", error=type(exc).__name__)
            return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        try:
            self._r.setex(PREFIX + key, self._ttl, json.dumps(value, default=str))
        except Exception as exc:  # noqa: BLE001
            log.warning("profile_cache_set_failed", error=type(exc).__name__)

    def invalidate(self, practitioner_id: str) -> None:
        """Drop every cached view of this doctor (all field types)."""
        try:
            for k in list(self._r.scan_iter(match=f"{PREFIX}{practitioner_id}:*")):
                self._r.delete(k)
        except Exception as exc:  # noqa: BLE001
            log.warning("profile_cache_invalidate_failed", error=type(exc).__name__)


def get_cache() -> ProfileCache | None:
    """A cache on the configured Redis, or ``None`` when there is none or it cannot be reached."""
    if settings.profile_cache_ttl_s <= 0:
        return None
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.3, socket_timeout=0.3)
        client.ping()
        return ProfileCache(client)
    except Exception as exc:  # noqa: BLE001
        log.info("profile_cache_unavailable", error=type(exc).__name__)
        return None
