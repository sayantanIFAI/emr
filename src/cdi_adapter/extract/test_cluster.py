"""Tests are written together: find them in the page's own text by where they sit, with no model involved.

A doctor writes the tests as a list, in one place (a box, a margin, the bottom of the page), often with no heading at all. So a
name's NEIGHBOURS are evidence about it:

- a STRONG test name (CBC, FBS, LFT, TSH ...) the lab lists place exactly is a test wherever it is written;
- an AMBIGUOUS name (vitamin D, calcium, iron ...) is also a supplement: it is a test only beside other test evidence, or when
  "25(OH)" is written with it. On its own, or among medicine lines, it is not taken;
- a ONE-LETTER slip of a strong abbreviation ("fas" for FBS: handwriting makes b look like a) is taken only inside a group of
  lines that also holds other test evidence, and is always marked "read as ...".

Position decides what is "beside": lines whose boxes are close (a line or so apart, and near in width) form one group. Every
name still has to pass the lab-test gate afterwards, and nothing found here is ever accepted without a person."""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any

from . import lab_mapping, lab_resolve
from .test_names import _STRONG, is_known_test, looks_like_medicine, split_tests

# a line with the marks of a medicine order is not looked at (and is never part of a group)
_MEDICINE_LINE = re.compile(r"(?i)\b(?:tabs?|tablets?|caps?|capsules?|syp|syr|inj|drops?|oint|cream|gel|susp)\b\.?"
                            r"|\d\s*(?:mg|mcg|ml|gm|iu)\b|\b\d+\s*tabs?\b")
# a word that also names a supplement: the name is a test only beside other test evidence
_WEAK_WORDS = frozenset("vitamin vit iron calcium zinc magnesium folic potassium sodium pt".split())      # "pt" is also "patient"
# "25(OH)", "25 OH", "2OH" (the 5 lost): the vitamin D test is written, whatever else the line says
_MARKER = re.compile(r"(?i)(?<![0-9])(?:25|2)\s*[\(\[]?\s*oh\b")

# letters handwriting makes look alike (one can be read as another); both directions
_PAIRS = ("ab", "ao", "ae", "bh", "b6", "ce", "co", "eo", "go", "gq", "hn", "il", "lt", "mn", "nu", "nr", "ps", "pb", "rs", "s5", "uv", "vy", "tf")
_CONFUSABLE: set[tuple[str, str]] = {p for a, b in _PAIRS for p in ((a, b), (b, a))}


@dataclass(frozen=True)
class Found:
    test: str          # the name to list (what the lab lists place)
    as_read: str       # what the readers wrote
    why: str           # "exact" | "beside" | "near" | "marker"

    @property
    def note(self) -> str:
        """Where it came from, in words for the screen."""
        return {"exact": "found in the page's text",
                "beside": f"found in the page's text beside other tests (read as '{self.as_read}')",
                "near": f"read as '{self.as_read}' (one letter from {self.test}), written beside other tests",
                "marker": f"'{self.as_read}' is written: the vitamin D test"}[self.why]


def placed_text(gram: str) -> str | None:
    """The name to list when the lab lists place these words EXACTLY (the mapping table, the national list, the gazetteer), or when
    a reading with ``?`` for unreadable letters fits exactly one standard test; otherwise None. A fuzzy match never counts."""
    if sum(ch.isalpha() for ch in gram) < 2:
        return None
    if "?" in gram:
        hit = lab_mapping.fit(gram)
        return hit.alias.capitalize() if hit else None
    rz = lab_resolve.resolve(gram)
    return gram if rz is not None and not getattr(rz, "fuzzy", False) else None


def _abbreviations() -> frozenset[str]:
    """The strong abbreviations a slip is compared with: 3 to 6 letters, a test and nothing else."""
    out = {w for w in _STRONG if 3 <= len(w) <= 6 and w.isalpha()}
    for key in lab_mapping._load():                                   # noqa: SLF001 - the table's own keys
        if " " not in key and 3 <= len(key) <= 6 and key.isalpha() and key not in _WEAK_WORDS:
            out.add(key)
    return frozenset(out)


def near_miss(token: str) -> str | None:
    """The ONE strong abbreviation this word is a single handwriting-confusable letter away from (``fas`` -> ``FBS``), else None.
    Words that already are a test, and words with two or more differing letters, are not slips."""
    w = token.casefold()
    if not (3 <= len(w) <= 6 and w.isalpha()):
        return None
    abbr = _abbreviations()
    if w in abbr:
        return None
    hits = []
    for a in abbr:
        if len(a) == len(w):
            diff = [(x, y) for x, y in zip(w, a) if x != y]
            if len(diff) == 1 and diff[0] in _CONFUSABLE:
                hits.append(a)
        elif len(a) == len(w) + 1 and any(a[:k] + a[k + 1:] == w and a[k] == a[k - 1] for k in range(1, len(a))):
            hits.append(a)                                         # a doubled letter written once ("apt" for APTT)
    return hits[0].upper() if len(hits) == 1 else None             # two ways to read it: it is not taken


@dataclass
class _Hit:
    test: str
    as_read: str
    kind: str          # "strong" | "weak" | "near" | "marker"


def _line_hits(line: str, long_rule: bool = True) -> list[_Hit]:
    """Every test-like thing in one line: the longest run of up to three words the lists place wins, and the line goes on."""
    out: list[_Hit] = []
    sentence = len(re.findall(r"[A-Za-z0-9?]+", line)) > 4
    for piece in split_tests(line):
        words = re.findall(r"[A-Za-z0-9?]+", piece)
        i = 0
        while i < len(words):
            for n in (3, 2, 1):
                gram = " ".join(words[i:i + n])
                got = placed_text(gram) if i + n <= len(words) else None
                if got:
                    weak = any(w.casefold() in _WEAK_WORDS for w in (*gram.split(), *got.split()))
                    if weak and sentence:
                        i += n                                  # an ambiguous name inside a sentence ("calcium rich diet ...") is not a list entry
                        break
                    out.append(_Hit(got, gram, "weak" if weak else "strong"))
                    i += n
                    break
            else:
                slip = near_miss(words[i])
                if slip:
                    out.append(_Hit(slip, words[i], "near"))
                i += 1
    # the same slip written twice in a list ("fas, fas") is two entries: split_tests listed it once, so the repeats are added back
    for slip_hit in [x for x in out if x.kind == "near"]:
        written = len(re.findall(r"(?i)(?<![a-z])" + re.escape(slip_hit.as_read) + r"(?![a-z])", line))
        for _ in range(written - 1):
            out.append(_Hit(slip_hit.test, slip_hit.as_read, "near"))
    total = len(re.findall(r"[A-Za-z0-9?]+", line))
    if long_rule and total >= 6 and sum(h.kind == "strong" for h in out) / total < 0.25:
        # a long line that is mostly other words (the clinic's printed list of services, a sentence): a test name inside it is weak
        # evidence, like an ambiguous name (MEASURED: "ECG" in a printed footer "Endoscopy Ultrasonography Echocardiography ...")
        out = [_Hit(h.test, h.as_read, "weak") if h.kind == "strong" else h for h in out]
    m = _MARKER.search(line)
    if m:
        out.append(_Hit("Vitamin D", m.group(0), "marker"))               # beside or not, "25(OH)" is the vitamin D test
    return out


def _box(b: dict[str, Any]) -> tuple[float, float, float, float] | None:
    try:
        x0, y0, x1, y1 = (float(v) for v in b["bbox"][:4])
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _groups(items: list[tuple[int, dict[str, Any]]]) -> list[list[int]]:
    """Groups of lines (indexes into ``items``) that sit close together. Close = centres within 1.6 line heights vertically and the
    boxes within 3 line heights sideways; a box more than 3 line heights tall (a loose box around a whole paragraph) joins nothing.
    Lines without a box are grouped with the line before and after them in reading order."""
    boxes = [_box(b) for _, b in items]
    heights = [bb[3] - bb[1] for bb in boxes if bb]
    lh = min(60.0, max(12.0, statistics.median(heights))) if heights else 30.0
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = boxes[i], boxes[j]
            if a is None or b is None:
                if j == i + 1:
                    join(i, j)                                    # reading order is all there is
                continue
            if (a[3] - a[1]) > 3 * lh or (b[3] - b[1]) > 3 * lh:
                continue
            dy = abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2)
            gap = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
            if dy <= 1.6 * lh and gap <= 3 * lh:
                join(i, j)
    out: dict[int, list[int]] = {}
    for i in range(len(items)):
        out.setdefault(find(i), []).append(i)
    return list(out.values())


def scan(blocks: list[dict[str, Any]] | None) -> list[Found]:
    """The tests the page's text holds, by position (see the module text). Medicine lines are never looked at. Candidates only."""
    items = [(i, b) for i, b in enumerate(blocks or []) if str(b.get("text") or "").strip()
             and not _MEDICINE_LINE.search(str(b["text"])) and not looks_like_medicine(str(b["text"]))]
    hits = [_line_hits(str(b["text"])) for _, b in items]
    found: list[tuple[int, Found]] = []
    for group in _groups(items):
        allh = [h for g in group for h in hits[g]]
        strong = sum(h.kind == "strong" for h in allh)
        marker = sum(h.kind == "marker" for h in allh)
        weak = sum(h.kind == "weak" for h in allh)
        near = sum(h.kind == "near" for h in allh)
        for g in group:
            for h in hits[g]:
                if h.kind == "strong":
                    f = Found(h.test, h.as_read, "exact")
                elif h.kind == "marker":
                    f = Found(h.test, h.as_read, "marker")
                elif h.kind == "weak" and strong + near + marker >= 1:
                    f = Found(h.test, h.as_read, "beside")
                elif h.kind == "near" and strong + marker + weak + (near - 1) >= 1:
                    f = Found(h.test, h.as_read, "near")
                else:
                    continue
                found.append((items[g][0], f))
    found.sort(key=lambda kv: kv[0])                                   # the page's reading order
    seen: set[str] = set()
    out: list[Found] = []
    for _, f in found:
        mapped = lab_mapping.lookup(f.test)                              # "vit D" and "Vitamin D" are one test
        k = (mapped.canonical if mapped else re.sub(r"[^a-z0-9]", "", f.test.casefold())).casefold()
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the model's own test entries, put right by the same rules. MEASURED on a real prescription: "Chest ECG" was read "Chest ECO" and
# "Na+ & K+ Level Test" was read "Nat & Kit Level Test"; neither was placed by the lab lists, so both showed as "outside the lab list".
# Na+ & K+ as handwriting gets read: the + comes out as t ("Nat"), the & as "-2" / "+ q" / q, the N as NP; "Kit" for "K+".
# MEASURED on a real page: "NAT & Kit Level Test", "NAT-2 Kit Level Th", "NAT + Q Kit Level Test", "NPT & K+ Level F".
_ELECTROLYTE_CORE = r"(?:s[.\s]*)?n[ap]\s*[+t]?\s*(?:&|and|-?\s*2|\+\s*q|[+,/q])\s*k\s*[+it]{0,2}\b"
_ELECTROLYTE = re.compile(r"(?i)^\W*" + _ELECTROLYTE_CORE)
_ELECTROLYTE_IN = re.compile(r"(?i)\b" + _ELECTROLYTE_CORE)


def answer_tests(text: str) -> list[str]:
    """The tests a model's answer holds, WORD by word, however garbled the rest of the string is: ``"CBC w. NPT & K+ Level F"`` holds CBC
    and sodium-and-potassium. MEASURED on a real page: the enlarged views read CBC inside strings that differed every time
    ("CBC w. diff.", "CBC + WBC", "CBC w. NPT & K+ ..."), so comparing whole strings never found it read in two views."""
    out: list[str] = []
    for h in _line_hits(text, long_rule=False):
        if h.kind != "strong":
            continue
        mapped = lab_mapping.lookup(h.test)
        name = "Na+ & K+" if mapped and mapped.canonical.startswith("Sodium and potassium") else h.test      # "Na K" and "Na+ & K+" are one test
        if name not in out:
            out.append(name)
    if _ELECTROLYTE_IN.search(text) and "Na+ & K+" not in out:
        out.append("Na+ & K+")
    return out


def repair_piece(piece: str, evidence: bool) -> tuple[str, str] | None:
    """A test the lists do not place, put right: sodium and potassium written "Na+ & K+" (any reading of the + signs), or a word that is a
    one-letter handwriting slip of a strong abbreviation ("ECO" -> ECG) when ``evidence`` says other tests are listed beside it. The
    corrected name must be PLACED by the lists, and the note says what was read. ``None`` when nothing applies."""
    if placed_text(piece):
        return None
    if _ELECTROLYTE.match(piece):
        return "Na+ & K+", f"read as '{piece}' (sodium and potassium)"
    if not evidence:
        return None
    fixed: list[str] = []
    slips: list[str] = []
    for w in piece.split():
        slip = near_miss(re.sub(r"[^A-Za-z]", "", w))
        if slip and not placed_text(w):
            fixed.append(slip)
            slips.append(slip)
        else:
            fixed.append(w)
    name = " ".join(fixed)
    if slips and placed_text(name):
        return name, f"read as '{piece}' (one letter from {slips[0]})"
    return None


def repair_investigations(payload: dict[str, Any]) -> dict[str, str]:
    """Apply ``repair_piece`` to every test the model listed (in place). Returns ``{corrected name: note}`` so the result can say what was
    read. Evidence for a slip: at least one other listed test the lists place exactly."""
    inv = payload.get("investigations")
    if not isinstance(inv, list):
        return {}

    def text_of(x: Any) -> str:
        return str(x.get("text") or "") if isinstance(x, dict) else (x if isinstance(x, str) else "")

    entries = [(i, [seg for seg in re.split(r"\s*[,;]\s*", text_of(x)) if seg.strip()]) for i, x in enumerate(inv)]     # "Nat & Kit" is one test: not cut at the &
    placed = sum(1 for _, segs in entries for seg in segs for p in split_tests(seg) if placed_text(p) or is_known_test(p))     # "CBC Test" counts: CBC is a test word
    notes: dict[str, str] = {}
    for i, segs in entries:
        new: list[str] = []
        changed = False
        for seg in segs:
            whole = repair_piece(seg, evidence=placed >= 1) if _ELECTROLYTE.match(seg) else None
            if whole:
                new.append(whole[0])
                notes[whole[0]] = whole[1]
                changed = True
                continue
            for p in split_tests(seg):
                r = repair_piece(p, evidence=placed >= 1)
                if r:
                    new.append(r[0])
                    notes[r[0]] = r[1]
                    changed = True
                else:
                    new.append(p)
        if changed:
            text = ", ".join(new)
            inv[i] = {**inv[i], "text": text} if isinstance(inv[i], dict) else text
    return notes
