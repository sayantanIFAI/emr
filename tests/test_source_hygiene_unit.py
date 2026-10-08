"""No source file carries a stray control character (an escape written wrongly once turned \b into a backspace)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {9, 10, 13, 12}          # tab, newline, carriage return, form feed


def test_no_source_file_has_a_stray_control_character():
    bad = []
    for pattern in ("src/**/*.py", "tests/**/*.py", "schemas/*.json", "infra/**/*.sh", "db/**/*.py"):
        for p in ROOT.glob(pattern):
            data = p.read_bytes()
            if any(b < 32 and b not in ALLOWED for b in data):
                bad.append(str(p.relative_to(ROOT)))
    assert bad == [], bad
