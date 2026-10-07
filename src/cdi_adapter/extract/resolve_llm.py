"""The model-assisted half of the hybrid: choose among reference names, never invent one.

The deterministic gate (lab_resolve / indian_codes) answers when it can. For a test name it cannot place but that is
close to one or more reference names (a misread letter in a handwritten abbreviation), ONE model call shows the page
and the candidates and asks which of them is written, or none. The answer is accepted only when

* it is the number of one of the offered candidates (nothing else can come out), and
* the candidate is textually close to what was read (a model cannot turn "hepatology" into a lab test), and

and what is stored stays marked as chosen by the model, so it is never presented as a plain reading.
"""
from __future__ import annotations

import difflib
from typing import Any

from ..config import settings
from ..logging import get_logger
from . import lab_resolve, medicine_resolve
from .indian_codes import norm
from .test_names import is_known_test, looks_like_medicine

log = get_logger(__name__)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"choices": {"type": "array", "items": {"type": ["integer", "null"]}}},
    "required": ["choices"],
}
MIN_SIMILARITY = 0.5
MAX_ITEMS = 12


def pending(tests: list[str]) -> list[tuple[str, list[str]]]:
    """Tests the gate cannot place, with the reference names they might be (only those with some)."""
    out: list[tuple[str, list[str]]] = []
    for t in dict.fromkeys(tests):
        if lab_resolve.resolve(t) is not None or is_known_test(t) or looks_like_medicine(t):
            continue
        cands = lab_resolve.suggest(t, k=5)
        if cands:
            out.append((t, cands))
    return out[:MAX_ITEMS]


def prompt_for(items: list[tuple[str, list[str]]]) -> str:
    lines = []
    for i, (written, cands) in enumerate(items, start=1):
        opts = "  ".join(f"{j}) {c}" for j, c in enumerate(cands, start=1))
        lines.append(f"{i}. read as '{written}' -> {opts}")
    return (
        "A doctor's handwritten prescription is shown. Some lab test names on it were read imperfectly. For each "
        "numbered item below choose which candidate is what the HANDWRITING on the page says, or null if none of them "
        "is clearly written there. Do not choose a candidate only because it is a common test; look at the writing.\n"
        + "\n".join(lines)
        + '\nAnswer ONLY as JSON: {"choices": [one entry per item: the candidate number or null]}'
    )


def accept(written: str, cands: list[str], choice: Any) -> str | None:
    if not isinstance(choice, int) or isinstance(choice, bool) or not 1 <= choice <= len(cands):
        return None
    cand = cands[choice - 1]
    if difflib.SequenceMatcher(None, norm(written), norm(cand)).ratio() < MIN_SIMILARITY:
        return None
    return cand


def resolve_tests(client: Any, image: bytes, tests: list[str]) -> dict[str, str]:
    """{as read: reference name the model chose}. Empty when nothing is pending or the call fails."""
    if not settings.llm_resolve_enabled:
        return {}
    items = pending(tests)
    if not items:
        return {}
    try:
        resp, _ = client.vlm_json_ex(image, prompt_for(items), SCHEMA, max_tokens=120, retries=1)
    except Exception as exc:  # noqa: BLE001 - an extra look must never cost the document
        log.warning("llm_resolve_failed", error=str(exc)[:200])
        return {}
    choices = (resp or {}).get("choices")
    if not isinstance(choices, list):
        return {}
    out: dict[str, str] = {}
    for (written, cands), choice in zip(items, choices):
        got = accept(written, cands, choice)
        if got:
            out[written] = got
    log.info("llm_resolve", asked=len(items), chosen=len(out))
    return out


FOLLOWUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"tests": {"type": "array", "items": {"type": "string"}}},
    "required": ["tests"],
}


def followup_prompt(follow_up: str) -> str:
    return chr(10).join([
        "A doctor's prescription is shown. It has this follow-up instruction: " + repr(follow_up[:160]) + ".",
        "Often the doctor writes the lab tests to be done before that visit right next to it, in brackets or braces "
        "(for example: review after 2 weeks with {HbA1c / FBS / TSH}). List every lab test or investigation that is "
        "WRITTEN next to or after that follow-up instruction, one string per test, exactly as written. If none is written "
        "there, return an empty list. Do not list medicines. Do not add a test that is not written.",
        'Answer ONLY as JSON: {"tests": ["...", "..."]}'])


def followup_tests(client: Any, image: bytes, follow_up: str | None, known: list[str]) -> list[str]:
    """Tests written with the follow-up line that the full-page answer missed. Only plain strings with a letter, at most 12,
    none already listed; everything that comes back still goes through the normal checks and is never auto-accepted."""
    from .test_names import split_tests

    if not settings.followup_second_look or not follow_up or not str(follow_up).strip():
        return []
    try:
        resp, _ = client.vlm_json_ex(image, followup_prompt(str(follow_up)), FOLLOWUP_SCHEMA, max_tokens=120, retries=1)
    except Exception as exc:  # noqa: BLE001 - an extra look must never cost the document
        log.warning("followup_second_look_failed", error=str(exc)[:200])
        return []
    got = (resp or {}).get("tests")
    if not isinstance(got, list):
        return []
    have = {norm(k) for k in known}
    out: list[str] = []
    for item in got:
        if not isinstance(item, str):
            continue
        for one in split_tests(item):
            if 2 <= len(one) <= 40 and any(ch.isalpha() for ch in one) and norm(one) not in have \
                    and not medicine_resolve.advice_like(one) and not (medicine_resolve.known(one) and not is_known_test(one)):
                have.add(norm(one))
                out.append(one)
    log.info("followup_second_look", found=len(out))
    return out[:12]


def prompt_for_medicines(items: list[tuple[str, list[str]]]) -> str:
    lines = []
    for i, (written, cands) in enumerate(items, start=1):
        opts = "  ".join(f"{j}) {c}" for j, c in enumerate(cands, start=1))
        lines.append(f"{i}. read as '{written}' -> {opts}")
    head = ("A doctor's handwritten prescription is shown. Some medicine names on it were read imperfectly. For each "
            "numbered item below choose which candidate is what the HANDWRITING on the page says, or null if none of them "
            "is clearly written there. Do not choose a candidate only because it is a common medicine; look at the "
            "letters actually written.")
    tail = 'Answer ONLY as JSON: {"choices": [one entry per item: the candidate number or null]}'
    return chr(10).join([head, *lines, tail])


def resolve_medicines(client: Any, image: bytes, names: list[str]) -> dict[str, str]:
    """{medicine as read: reference name the model chose}. Same limits as for tests: only an offered candidate that is
    textually close to the reading, never free text; empty when nothing is pending or the call fails."""
    if not settings.llm_resolve_enabled:
        return {}
    items = medicine_resolve.pending(names)
    if not items:
        return {}
    try:
        resp, _ = client.vlm_json_ex(image, prompt_for_medicines(items), SCHEMA, max_tokens=120, retries=1)
    except Exception as exc:  # noqa: BLE001 - an extra look must never cost the document
        log.warning("llm_resolve_medicines_failed", error=str(exc)[:200])
        return {}
    choices = (resp or {}).get("choices")
    if not isinstance(choices, list):
        return {}
    out: dict[str, str] = {}
    for (written, cands), choice in zip(items, choices):
        got = accept(medicine_resolve.first_word(written), cands, choice)
        if got:
            out[written] = got
    log.info("llm_resolve_medicines", asked=len(items), chosen=len(out))
    return out
