"""Is this word a medicine? A reference check from a list of medicine names (brands and generics).

The list is built from a medicine dataset by ``scripts/build_medicine_lexicon.py`` and lives OUTSIDE the repository
(``settings.medicine_lexicon_path``); without it every check says "not known" and the other rules still apply.
Matching tolerates a misread letter or two, because a handwritten brand name is rarely read exactly.
"""
from __future__ import annotations

import difflib
import re
import threading
from collections import defaultdict
from pathlib import Path

from ..config import settings

_WORD = re.compile(r"[A-Za-z]{4,}")
# the drug name is the first word after a form prefix ("Inj", "Tab", "In" for a misread Inj ...)
_PREFIX = re.compile(r"^\s*(?:inj|in|tab|t|cap|c|syp|sy|syr|susp|oint|cream|gel|drops?|neb|inh|iv|im|sc|po|rx)\b[\s.:\-]*", re.I)
_lock = threading.Lock()
_cache: dict[str, "Lexicon"] = {}


class Lexicon:
    def __init__(self, words: set[str]) -> None:
        self.words = words
        self.buckets: dict[tuple[str, int], list[str]] = defaultdict(list)    # (first letter, length) -> words
        for w in words:
            self.buckets[(w[0], len(w))].append(w)

    def match(self, word: str) -> str | None:
        """The medicine word this one is (exactly, or within a misread letter or two), else None."""
        w = word.lower()
        if len(w) < 4:
            return None
        if w in self.words:
            return w
        if len(w) < 6:
            return None                  # a short word one letter off is too often a different word
        sm = difflib.SequenceMatcher(None, "", w)          # seq2 = w is cached across candidates
        best, best_score = None, 0.0
        for n in (len(w) - 1, len(w), len(w) + 1):
            for cand in self.buckets.get((w[0], n), ()):
                sm.set_seq1(cand)
                if sm.real_quick_ratio() < 0.86 or sm.quick_ratio() < 0.86:
                    continue
                s = sm.ratio()
                if s > best_score:
                    best, best_score = cand, s
        return best if best is not None and best_score >= 0.86 else None


def load(path: str | None = None) -> Lexicon | None:
    p = path or settings.medicine_lexicon_path
    if not p:
        return None
    with _lock:
        if p in _cache:
            return _cache[p]
        f = Path(p)
        if not f.is_file():
            return None
        lex = Lexicon({ln.strip().lower() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()})
        _cache[p] = lex
        return lex


def medicine_match(text: str | None, lex: Lexicon | None = None) -> str | None:
    """The medicine word found in ``text`` (a brand or generic name, misreadings allowed), or None."""
    lex = lex or load()
    if lex is None or not text:
        return None
    words = _WORD.findall(_PREFIX.sub("", text, count=1))
    return lex.match(words[0]) if words else None          # the drug name: the first word after the form prefix
