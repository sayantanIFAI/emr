"""Clinical field grammars (E15-S2) + numeric-context normalisation.

The grammars reject impossible *shapes*; they never invent a value. They are applied
(1) as a validator on every recognizer's output and on extracted medication fields, and
(2) by the grammar-locked decoding path of E2-S3 when that backend is enabled.

Numeric-context normalisation maps OCR look-alikes (O->0, l/I/|->1, S->5 ...) ONLY inside a
token that is already mostly digits, so "Telma 4O" compares equal to "Telma 40" while the
drug name itself is never rewritten.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CONFUSABLE_IN_NUMBER = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0",
                                       "l": "1", "I": "1", "|": "1", "i": "1",
                                       "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2"})
_TOKEN = re.compile(r"\S+")
_UNITS = {"mg", "g", "mcg", "ug", "µg", "ml", "iu", "u", "%", "mg/ml", "mg/5ml", "gm", "units"}
_FREQ = {"od": 1, "qd": 1, "hs": 1, "bd": 2, "bid": 2, "tds": 3, "tid": 3, "qid": 4,
         "sos": 0, "prn": 0, "stat": 0, "qhs": 1, "ac": None, "pc": None}
_HALF = {"½": 0.5, "1/2": 0.5}


def _mostly_digits(tok: str) -> bool:
    core = re.sub(r"[.,/\-x×]", "", tok)
    if not core:
        return False
    digits = sum(ch.isdigit() for ch in core)
    return digits >= 1 and digits / len(core) >= 0.5


def normalize_numeric_context(text: str) -> str:
    """Fix digit look-alikes inside numeric tokens only; collapse whitespace; casefold."""
    out = []
    for tok in _TOKEN.findall(text or ""):
        # split a glued unit ("40mg") so the number part can be judged on its own
        m = re.match(r"^([0-9OoDQlI|iSsBZz.,/\-½]+)([a-zA-Z%µ/]*)$", tok)
        if m and _mostly_digits(m.group(1)):
            tok = m.group(1).translate(_CONFUSABLE_IN_NUMBER) + m.group(2)
        out.append(tok)
    return " ".join(out).casefold()


def numbers_in(text: str) -> list[str]:
    """Numeric tokens after look-alike normalisation - these must match EXACTLY."""
    norm = normalize_numeric_context(text)
    return re.findall(r"\d+(?:\.\d+)?(?:/\d+)?|½", norm)


@dataclass
class GrammarResult:
    valid: bool
    normalized: object = None
    reason: str | None = None


def parse_strength(text: str | None) -> GrammarResult:
    """<number><unit>  e.g. '40 mg', '0.5mg', '500 MG', '5 ml'."""
    if not text:
        return GrammarResult(False, None, "empty")
    t = normalize_numeric_context(text).replace(" ", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(mg|g|gm|mcg|ug|µg|ml|iu|u|units|%|mg/ml|mg/5ml)", t)
    if not m:
        return GrammarResult(False, None, f"strength must be <number><unit>, got {text!r}")
    return GrammarResult(True, (float(m.group(1)), m.group(2)))


def parse_dose_pattern(text: str | None) -> GrammarResult:
    """d-d-d or d-d-d-d with optional ½ (e.g. 1-0-1, ½-0-½, 1-1-1-1)."""
    if not text:
        return GrammarResult(False, None, "empty")
    t = normalize_numeric_context(text).replace(" ", "").replace("—", "-").replace("–", "-")
    parts = t.split("-")
    if len(parts) not in (3, 4):
        return GrammarResult(False, None, f"dose pattern must be d-d-d(-d), got {text!r}")
    vals: list[float] = []
    for p in parts:
        if p in _HALF:
            vals.append(_HALF[p])
        elif re.fullmatch(r"\d", p):
            vals.append(float(p))
        else:
            return GrammarResult(False, None, f"dose slot {p!r} is not a digit or ½")
    if any(v > 4 for v in vals):
        return GrammarResult(False, None, "dose slot above 4 units is implausible")
    return GrammarResult(True, tuple(vals))


def parse_frequency(text: str | None) -> GrammarResult:
    """Closed set (OD BD TDS QID HS SOS PRN ...) or a d-d-d pattern -> per-day count."""
    if not text:
        return GrammarResult(False, None, "empty")
    key = re.sub(r"[^a-z]", "", text.casefold())
    if key in _FREQ:
        return GrammarResult(True, _FREQ[key])
    dp = parse_dose_pattern(text)
    if dp.valid:
        return GrammarResult(True, sum(1 for v in dp.normalized if v > 0))
    return GrammarResult(False, None, f"frequency {text!r} is not in the closed set")


def parse_duration(text: str | None) -> GrammarResult:
    """x5d | x 5 days | 5 days | x 2 wk | x 1/52 | x 1 month -> days."""
    if not text:
        return GrammarResult(False, None, "empty")
    t = normalize_numeric_context(text).replace("×", "x")
    m = re.search(r"(\d+)\s*/\s*52", t)
    if m:
        return GrammarResult(True, int(m.group(1)) * 7)
    m = re.search(r"(\d+)\s*(d|day|days|w|wk|wks|week|weeks|m|mo|month|months)\b", t)
    if not m:
        return GrammarResult(False, None, f"duration {text!r} does not match x<n> d|wk|mo")
    n, u = int(m.group(1)), m.group(2)
    days = n * (7 if u.startswith("w") else 30 if u.startswith("m") else 1)
    if days > 3650:
        return GrammarResult(False, None, "duration above 10 years is implausible")
    return GrammarResult(True, days)


def check_medication_fields(md: dict) -> list[tuple[str, str]]:
    """(field, reason) for every medication field whose value breaks its grammar.
    Only fields that are present are checked - absence is S6's job (check_medication)."""
    bad: list[tuple[str, str]] = []
    if md.get("strength_num") is not None and md.get("strength_unit"):
        unit = str(md["strength_unit"]).casefold()
        if unit not in _UNITS:
            bad.append(("strength", f"unit {md['strength_unit']!r} is not a dose unit"))
    freq = md.get("frequency_code")
    if freq and not parse_frequency(freq).valid:
        bad.append(("frequency", parse_frequency(freq).reason or "invalid"))
    return bad
