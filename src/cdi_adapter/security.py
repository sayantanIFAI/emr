"""Start-up guard against default credentials (ENT-S2).

The published defaults (``cdiadmin`` / ``cdiadminsecret`` for the object store, the ``cdi:cdi`` database
login, a short admin password) exist so a developer can run the stack on a laptop. Any environment other
than ``dev`` refuses to start while one of them is still in force, and the message names the setting.
"""
from __future__ import annotations

from urllib.parse import unquote, urlsplit

from .config import Settings, settings

_DEFAULT_S3 = {"cdiadmin", "cdiadminsecret", "minioadmin", "admin", "password"}
_DEFAULT_DB_PASSWORDS = {"cdi", "postgres", "password", "admin", ""}
MIN_ADMIN_PASSWORD = 12


def default_credential_problems(s: Settings | None = None) -> list[str]:
    s = s or settings
    problems: list[str] = []
    if s.s3_access_key in _DEFAULT_S3:
        problems.append("CDI_S3_ACCESS_KEY is a published default")
    if s.s3_secret_key in _DEFAULT_S3:
        problems.append("CDI_S3_SECRET_KEY is a published default")
    try:
        pw = unquote(urlsplit(s.database_url).password or "")
    except ValueError:
        pw = ""
    if pw in _DEFAULT_DB_PASSWORDS:
        problems.append("CDI_DATABASE_URL uses a default or empty database password")
    if s.admin_password and len(s.admin_password) < MIN_ADMIN_PASSWORD:
        problems.append(f"CDI_ADMIN_PASSWORD is shorter than {MIN_ADMIN_PASSWORD} characters")
    return problems


def require_no_default_credentials(s: Settings | None = None) -> None:
    s = s or settings
    if s.env.strip().lower() == "dev":
        return
    problems = default_credential_problems(s)
    if problems:
        raise RuntimeError("Refusing to start with default credentials (CDI_ENV is not dev): "
                           + "; ".join(problems) + ". Set strong values in the environment, not in git.")
