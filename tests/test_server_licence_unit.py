"""The queue server may be Redis 7.2 or older (BSD-3-Clause) or Valkey; Redis 7.4+ / 8+ is refused (compliance/servers.py)."""
from __future__ import annotations

import pytest

from cdi_adapter.compliance import licences as L
from cdi_adapter.compliance import servers as S
from cdi_adapter.config import settings


@pytest.mark.parametrize("server", ["redis 6.2.14", "redis 7.0.15", "redis 7.2.4", "redis 7.2.11", "valkey 7.2.5", "valkey 8.0.1", "valkey 9.0.0"])
def test_bsd_servers_are_allowed(server):
    v = S.verdict(server)
    assert v.ok and v.licence == "BSD-3-Clause"


@pytest.mark.parametrize("server", ["redis 7.3.0", "redis 7.4.0", "redis 7.4.2", "redis 8.0.0", "redis 8.2.1"])
def test_redis_7_4_and_later_are_refused_with_the_reason(server):
    v = S.verdict(server)
    assert not v.ok and "RSALv2" in v.licence and "Valkey" in v.reason


@pytest.mark.parametrize("server", [None, "", "memcached 1.6", "redis"])
def test_a_server_that_cannot_be_read_is_not_accepted(server):
    assert not S.verdict(server).ok


def test_outside_dev_a_refused_server_stops_the_service(monkeypatch):
    monkeypatch.setattr(settings, "env", "runpod")
    with pytest.raises(S.ServerLicenceError, match="not allowed"):
        S.require_allowed("redis 8.0.0")
    assert S.require_allowed("redis 7.2.4").ok


def test_in_dev_it_is_a_warning_only_and_it_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "env", "dev")
    assert S.require_allowed("redis 8.0.0").ok is False                    # logged, not raised
    monkeypatch.setattr(settings, "env", "runpod")
    monkeypatch.setattr(settings, "server_licence_enforce", False)
    assert S.require_allowed("redis 8.0.0") is None


def test_an_unreachable_server_is_not_a_licence_question(monkeypatch):
    monkeypatch.setattr(settings, "env", "runpod")
    from cdi_adapter import swap

    class Down:
        def server(self): return None
    monkeypatch.setattr(swap, "resolve", lambda name: Down())
    assert S.require_allowed() is None


def test_the_notice_file_carries_the_redis_and_valkey_lines():
    text = L.notice(L.Report([], 0) if False else L.audit(distributions=[]))
    assert "Redis 7.2 or older" in text and "BSD-3-Clause" in text and "Valkey" in text and "NOT used" in text
