"""ENT-S2: no default credentials outside dev, and the message names the setting."""
from __future__ import annotations

import pytest

from cdi_adapter import security
from cdi_adapter.config import Settings


def _s(**kw) -> Settings:
    base = dict(env="runpod", s3_access_key="appK3y9x", s3_secret_key="s3cr3t-long-enough-9",
                database_url="postgresql+psycopg://cdi:Zx81kqPw@127.0.0.1:5432/cdi", admin_password="a-long-admin-pass")
    return Settings(**{**base, **kw})


def test_strong_values_start():
    security.require_no_default_credentials(_s())


@pytest.mark.parametrize("kw,name", [
    ({"s3_access_key": "cdiadmin"}, "CDI_S3_ACCESS_KEY"),
    ({"s3_secret_key": "cdiadminsecret"}, "CDI_S3_SECRET_KEY"),
    ({"database_url": "postgresql+psycopg://cdi:cdi@localhost:5432/cdi"}, "CDI_DATABASE_URL"),
    ({"database_url": "postgresql+psycopg://cdi@localhost:5432/cdi"}, "CDI_DATABASE_URL"),
    ({"admin_password": "short"}, "CDI_ADMIN_PASSWORD"),
])
def test_a_default_is_refused_and_the_setting_is_named(kw, name):
    with pytest.raises(RuntimeError, match=name):
        security.require_no_default_credentials(_s(**kw))


def test_dev_may_use_the_published_defaults():
    security.require_no_default_credentials(Settings(env="dev"))        # the laptop stack keeps working


def test_every_problem_is_listed_at_once():
    p = security.default_credential_problems(_s(s3_access_key="cdiadmin", s3_secret_key="cdiadminsecret"))
    assert len(p) == 2
