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
import re
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
        "Part of a doctor's prescription is shown (an enlarged piece of the page). It has this follow-up instruction: " + repr(follow_up[:160]) + ".",
        "Often the doctor writes the lab tests to be done before that visit right next to it, in brackets or braces "
        "(for example: review after 2 weeks with {HbA1c / FBS / TSH}). List every lab test or investigation that is "
        "WRITTEN next to or after that follow-up instruction, one string per test, exactly as written. If none is written "
        "there, return an empty list. Do not list medicines. Do not add a test that is not written.",
        'Answer ONLY as JSON: {"tests": ["...", "..."]}'])


def followup_region(image: bytes, blocks: list[dict[str, Any]] | None, follow_up: str) -> bytes:
    """The part of the page where the follow-up line is, enlarged: from just left of the line to the right edge and from
    a little above it to well below it (the tests are written beside it or on the line under it). The line is found by
    the best text match among the OCR blocks; with no match the lower 40% of the page is used. Small handwriting that
    the model misses on the whole page is often readable at twice the size."""
    import io

    import cv2
    import numpy as np

    arr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return image
    h, w = arr.shape[:2]
    best, score = None, 0.0
    n_fu = norm(follow_up)
    for b in blocks or []:
        t = norm(b.get("text"))
        if len(t) < 4 or not b.get("bbox"):
            continue
        r = difflib.SequenceMatcher(None, n_fu, t).ratio()
        if r > score:
            best, score = b, r
    if best is not None and score >= 0.4:
        x0, y0, _x1, y1 = (int(v) for v in best["bbox"])
        box = (max(0, x0 - 20), max(0, y0 - 40), w, min(h, y1 + max(110, int(0.1 * h))))
    else:
        box = (0, int(0.6 * h), w, h)
    crop = arr[box[1]:box[3], box[0]:box[2]]
    if crop.shape[1] < 1600:
        f = 1600 / crop.shape[1]
        crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    ok, png = cv2.imencode(".png", crop)
    return png.tobytes() if ok else image


def anywhere_prompt() -> str:
    return chr(10).join([
        "Part of a doctor's handwritten prescription is shown (it may be an enlarged piece of the page).",
        "List every LAB TEST, blood / urine test, scan or other investigation that the doctor has WRITTEN BY HAND for the "
        "patient to get done: in a list, in a margin, on its own line, or in brackets or braces beside a follow-up / review "
        "line (for example: review after 2 weeks {HbA1c / FBS / PPBS / TSH}). One string per test, exactly as written. "
        "Do NOT list medicines (tablets, capsules, injections, syrups), diagnoses, diet or other advice, and do NOT list "
        "anything that is PRINTED (a clinic's printed list of services, header or footer). If no test is written in this "
        "piece, return an empty list. Do not add a test that is not written.",
        'Answer ONLY as JSON: {"tests": ["...", "..."]}'])


def page_views(image: bytes) -> list[bytes]:
    """The page, and the places a doctor writes the tests, each enlarged: the lower part, the left side and the right side."""
    import cv2
    import numpy as np

    arr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return [image]
    h, w = arr.shape[:2]
    boxes = [(0, int(0.55 * h), w, h), (0, int(0.15 * h), int(0.5 * w), h), (int(0.5 * w), int(0.15 * h), w, h)]
    out = [image]
    for x0, y0, x1, y1 in boxes:
        crop = arr[y0:y1, x0:x1]
        f = max(1.0, 1400 / max(crop.shape[1], 1))
        if f > 1.0:
            crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        ok, png = cv2.imencode(".png", crop)
        if ok:
            out.append(png.tobytes())
    return out


_REVIEW_LINE = re.compile(r"(?i)\b(?:review|receive|revisit|f/?u|follow[\s-]?up|come)\b")
_AFTER_WORD = re.compile(r"(?i)\b(?:of|with|for)\b\s*[{\[(]?\s*|[{\[(]\s*")


def tests_from_lines(blocks: list[dict[str, Any]] | None, follow_up: str | None) -> list[str]:
    """Names written after a follow-up line's "of" / "with" / a bracket ("To review after 2 wks of HbA1c/PBS/PPBS/S. Lipase"),
    taken from the text the readers already produced. Candidates only: each still has to pass the lab-test gate."""
    texts = [str(b.get("text") or "") for b in blocks or []] + ([follow_up] if follow_up else [])
    out: list[str] = []
    for t in texts:
        m = _REVIEW_LINE.search(t)
        if not m:
            continue
        tail = _AFTER_WORD.search(t, m.end())
        if not tail:
            continue
        for part in re.split(r"\s*[/,;&{}\[\]()]\s*|\s+and\s+", t[tail.end():]):
            part = part.strip(" .")
            if 2 <= len(part) <= 40:
                out.append(part)
    return out


def followup_tests(client: Any, image: bytes, follow_up: str | None, known: list[str],
                   blocks: list[dict[str, Any]] | None = None) -> list[str]:
    """Tests the full-page answer missed. Lab tests are the point of the product and the doctor writes them anywhere (beside
    the follow-up line, down the left or right side, at the bottom), so the page is looked at again in several enlarged
    views at once (plus the follow-up line's own region when there is one). Only plain strings with a letter, at most 12,
    none already listed; everything that comes back still goes through the normal checks and is never auto-accepted."""
    from concurrent.futures import ThreadPoolExecutor

    from .test_names import split_tests

    if not settings.followup_second_look:
        return []
    jobs: list[tuple[bytes, str]] = [(v, anywhere_prompt()) for v in page_views(image)]
    if follow_up and str(follow_up).strip():
        try:
            jobs.append((followup_region(image, blocks, str(follow_up)), followup_prompt(str(follow_up))))
        except Exception as exc:  # noqa: BLE001
            log.warning("followup_region_failed", error=str(exc)[:200])

    jobs = [j for j in jobs for _ in range(max(1, settings.second_look_repeats))]      # the same view, asked again: answers differ

    def ask(job: tuple[bytes, str]) -> Any:
        try:
            resp, _ = client.vlm_json_ex(job[0], job[1], FOLLOWUP_SCHEMA, max_tokens=120, retries=1)
            return (resp or {}).get("tests")
        except Exception as exc:  # noqa: BLE001 - an extra look must never cost the document
            log.warning("followup_second_look_failed", error=str(exc)[:200])
            return None

    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        answers = list(pool.map(ask, jobs))
    got: list[Any] = []
    for a in answers:
        if isinstance(a, list):
            got.extend(a)
    got.extend(tests_from_lines(blocks, follow_up))                  # the tests written on the follow-up line itself
    have = {norm(k) for k in known}
    out: list[str] = []
    for item in got:
        if not isinstance(item, str):
            continue
        for one in split_tests(item):
            # the lab gate: a name found by looking harder is kept ONLY if the test lists place it (a known test word, or
            # the national lab list / gazetteer). A medicine line, a diagnosis or a misreading never gets in as a test.
            if 2 <= len(one) <= 40 and any(ch.isalpha() for ch in one) and norm(one) not in have \
                    and (is_known_test(one) or lab_resolve.resolve(one) is not None or lab_resolve.suggest(one, k=1, floor=0.72)) \
                    and not medicine_resolve.advice_like(one) and not (medicine_resolve.known(one) and not is_known_test(one)):
                have.add(norm(one))
                out.append(one)
    log.info("followup_second_look", views=len(jobs), found=len(out))
    return out[:12]


NAME_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"name": {"type": ["string", "null"]}}, "required": ["name"]}
NAME_SCALES = (1.0, 1.6, 2.4)


def name_prompt() -> str:
    """The patient's name is an Indian personal name. Saying so is what stops the model from reading an English word that looks
    alike (MEASURED on a real photo: without it "Chowdhury" was read as "Broadway" in 6 of 6 runs; with it never). No example
    names are given: a list of examples leaked into the answer ("Rajesh Das" for a name that is not on the page)."""
    return chr(10).join([
        "This is a line from a doctor's handwritten prescription in India. Write, letter by letter, the PATIENT'S NAME that "
        "follows 'For', 'Name' or 'Mr / Mrs / Ms / Smt / Shri'. The name is an Indian personal name, not an English word and "
        "not a place. Put a space between the first name and the surname. Do not write the title, the age or the sex. If the "
        "name is not readable, answer null.",
        'Answer ONLY as JSON: {"name": "..."}'])


def name_crops(image: bytes, blocks: list[dict[str, Any]] | None, name: str | None) -> list[bytes]:
    """The page line the patient's name is on, cut out and enlarged to each of ``NAME_SCALES`` (the name line is found as the
    OCR block that shares the most words with the name already read; with none, the top third of the page)."""
    import cv2
    import numpy as np

    from ..names import name_key

    arr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return []
    h, w = arr.shape[:2]
    toks = [t for t in name_key(name).split() if len(t) >= 3]
    best, score = None, 0
    for b in blocks or []:
        if not b.get("bbox"):
            continue
        bt = name_key(b.get("text")).split()
        s = sum(1 for t in toks if difflib.get_close_matches(t, bt, n=1, cutoff=0.7))
        if s > score:
            best, score = b, s
    if best is not None and score >= 1:
        x0, y0, x1, y1 = (int(v) for v in best["bbox"])
        px, py = max(30, int(0.03 * w)), max(14, int(0.012 * h))
        box = (max(0, x0 - px), max(0, y0 - py), min(w, x1 + px), min(h, y1 + py))
    else:
        box = (0, 0, w, int(0.35 * h))
    crop = arr[box[1]:box[3], box[0]:box[2]]
    out: list[bytes] = []
    for f in NAME_SCALES:
        c = crop if f == 1.0 else cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        ok, png = cv2.imencode(".png", c)
        if ok:
            out.append(png.tobytes())
    return out


def name_reads(client: Any, image: bytes, blocks: list[dict[str, Any]] | None, name: str | None) -> list[str]:
    """The patient's name read again from the name line at several sizes (at the same time). Failed or empty reads are left
    out. Nothing here decides the name: the caller compares the readings (``names.consensus``) and the front desk confirms it."""
    from concurrent.futures import ThreadPoolExecutor

    if not settings.name_reread:
        return []
    try:
        crops = name_crops(image, blocks, name)
    except Exception as exc:  # noqa: BLE001
        log.warning("name_crop_failed", error=str(exc)[:200])
        return []

    def ask(png: bytes) -> str | None:
        try:
            resp, _ = client.vlm_json_ex(png, name_prompt(), NAME_SCHEMA, max_tokens=40, retries=1)
        except Exception as exc:  # noqa: BLE001 - an extra look must never cost the document
            log.warning("name_reread_failed", error=str(exc)[:200])
            return None
        n = (resp or {}).get("name")
        return " ".join(n.split())[:80] if isinstance(n, str) and any(ch.isalpha() for ch in n) else None

    if not crops:
        return []
    with ThreadPoolExecutor(max_workers=len(crops)) as pool:
        got = list(pool.map(ask, crops))
    return [g for g in got if g]


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
    if not settings.llm_resolve_enabled or not settings.llm_resolve_medicines:
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
