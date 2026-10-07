"""Lab-test gazetteer: the names and abbreviations a doctor writes -> the test, and its LOINC code where it has one.

SNOMED and LOINC lookups do not understand how Indian prescriptions abbreviate (KFT, LFT, HbA1c, PPBS, R/E ...), so
the gate is a curated table, built from two files in ``data/``:

* ``lab_tests_loinc.csv``  a test, its short name(s), long name, category, specimen, LOINC code and what a panel holds;
* ``lab_catalogue.csv``    the lab's own panel / test / short-code list.

``lookup(text)`` returns the test an entry names (exactly, or within a misread letter or two), or None. An entry that
is not in the table is not shown as a lab test. A match on a TEST carries a LOINC code; a match on one analyte of a
panel (``Uric Acid`` of the kidney panel) only says "this is a test": the panel's code is never given to it.
"""
from __future__ import annotations

import csv
import difflib
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_DATA = Path(__file__).resolve().parents[3] / "data"
_lock = threading.Lock()
_cache: dict[str, "Gazetteer"] = {}

# how doctors write the same test (lower-case, punctuation as spaces); the target is a test_code of the LOINC table
_SYNONYMS = {
    "UR_RE": ["urine r e", "urine re", "urine routine", "urine r m", "urine routine examination", "urine examination",
              "urine analysis", "urinalysis"],
    "STOOL_RE": ["stool r e", "stool re", "stool routine", "stool examination"],
    "FBS": ["fasting sugar", "fasting blood sugar", "fasting blood glucose", "fbg", "blood sugar fasting", "sugar fasting"],
    "PPBS": ["pp sugar", "ppbg", "post prandial", "post prandial sugar", "post meal sugar", "2 hr pp"],
    "HBA1C": ["a1c", "hb a1c", "hb1ac", "hba1c", "glycosylated hb", "glycosylated haemoglobin", "glycated haemoglobin"],
    "VITD": ["vit d", "vitamin d", "vit d3", "vitamin d3", "25 oh vitamin d", "25 hydroxy vitamin d"],
    "VITB12": ["vit b12", "b12", "vitamin b12", "cyanocobalamin"],
    "TFT": ["thyroid profile", "thyroid function", "thyroid function test", "t3 t4 tsh", "thyroid"],
    "KFT": ["rft", "kft", "renal function", "renal function test", "kidney function", "kidney function test", "rft kft"],
    "LFT": ["liver function", "liver function test", "liver profile"],
    "LIPID": ["lipid", "lipids", "lipid panel", "lipid profile", "fasting lipid profile"],
    "CBC": ["hemogram", "haemogram", "complete blood count", "complete blood picture", "cbp", "blood count"],
    "ELECT": ["electrolytes", "serum electrolytes", "na k", "na k cl"],
    "PT_INR": ["pt", "inr", "pt inr", "prothrombin time"],
    "CRP": ["c reactive protein", "crp quantitative"],
    "HIV_ELISA": ["hiv", "hiv 1 2", "hiv 1 and 2", "hiv elisa"],
    "DENGUE_NS1": ["ns1", "dengue ns1 antigen", "dengue ag"],
    "MAL_RDT": ["malaria", "malaria parasite", "mp", "malaria antigen", "mp card"],
}
_GENERIC = frozenset("test tests level levels serum blood total quantitative qualitative profile panel s".split())
_PANEL_WORDS = ("test", "profile", "panel", "analysis", "microscopy", "tolerance", "count")
_SKIP_NAMES = frozenset("na biochemistry serology others other quality morphology motility parasite cast crystal".split())


def _strip(n: str) -> str:
    return " ".join(w for w in n.split() if w not in _GENERIC) or n


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").casefold())).strip()


@dataclass(frozen=True)
class Match:
    kind: str                   # "test" (the entry names the test itself) | "analyte" (one part of a panel) | "catalogue"
    test_code: str | None
    long_name: str | None
    loinc: str | None
    category: str | None
    specimen: str | None
    alias: str                  # which name matched
    fuzzy: bool                 # matched within a misread letter, not exactly


class Gazetteer:
    def __init__(self) -> None:
        self.alias: dict[str, tuple[str, dict | None]] = {}          # normalised name -> (kind, row)
        self.buckets: dict[tuple[str, int], list[str]] = defaultdict(list)

    def _add(self, name: str | None, kind: str, row: dict | None) -> None:
        n = norm(name)
        if len(n) < 2 or n in _SKIP_NAMES:
            return
        # a test name beats an analyte name beats a catalogue-only name when two collide
        rank = {"test": 3, "analyte": 2, "catalogue": 1}
        for key in {n, _strip(n)}:
            if len(key) >= 2 and not (key in self.alias and rank[self.alias[key][0]] >= rank[kind]):
                self.alias[key] = (kind, row)

    def finish(self) -> None:
        for n in self.alias:
            self.buckets[(n[0], len(n))].append(n)

    def lookup(self, text: str | None) -> Match | None:
        n = norm(text)
        if not n:
            return None
        bare = norm(re.sub(r"\(.*?\)", " ", text or ""))                  # "Absolute Eosinophil Count (AEC)" -> without (AEC)
        for cand in (n, bare, _strip(n), _strip(bare)):
            if cand and cand in self.alias:
                return self._match(cand, fuzzy=False)
        n = _strip(bare) if len(bare) >= 5 else n
        if len(n) >= 5:                                              # a misread letter: only for names, never abbreviations
            sm = difflib.SequenceMatcher(None, "", n)
            best, best_score = None, 0.0
            for length in (len(n) - 1, len(n), len(n) + 1):
                for cand in self.buckets.get((n[0], length), ()):
                    if len(cand) < 5:
                        continue
                    sm.set_seq1(cand)
                    if sm.real_quick_ratio() < 0.88 or sm.quick_ratio() < 0.88:
                        continue
                    r = sm.ratio()
                    if r > best_score:
                        best, best_score = cand, r
            if best is not None and best_score >= 0.88:
                return self._match(best, fuzzy=True)
        return None

    def _match(self, name: str, *, fuzzy: bool) -> Match:
        kind, row = self.alias[name]
        row = row or {}
        return Match(kind, row.get("test_code"), row.get("long_name"), row.get("loinc_code") or None,
                     row.get("category"), row.get("specimen_type"), name, fuzzy)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]


def build(data_dir: Path | None = None) -> Gazetteer:
    d = data_dir or _DATA
    g = Gazetteer()
    loinc = _read(d / "lab_tests_loinc.csv") if (d / "lab_tests_loinc.csv").is_file() else []
    by_code = {r["test_code"]: r for r in loinc}
    for r in loinc:
        g._add(r["test_code"].replace("_", " "), "test", r)
        for part in re.split(r"\s*/\s*|\s*&\s*", r["short_name"]):
            g._add(part, "test", r)
        g._add(r["short_name"], "test", r)
        g._add(r["long_name"], "test", r)
        g._add(re.sub(r"\s*\(.*?\)", "", r["long_name"]), "test", r)
        for part in re.split(r"\s*/\s*", r["long_name"]):
            g._add(re.sub(r"\s*\(.*?\)", "", part), "test", r)
    for code, names in _SYNONYMS.items():
        if code in by_code:
            for nm in names:
                g._add(nm, "test", by_code[code])
    for r in loinc:                                   # what a panel holds: a test name, but not the panel's code
        notes = r.get("common_parameters_or_notes", "")
        if notes.count(",") >= 1 and not re.search(r"\b(screening|monitoring|marker|detection|assessment|evaluation|average)\b", notes, re.I):
            for part in re.split(r",\s*|/", notes):
                p = re.sub(r"\s*\(.*?\)", "", part).strip()
                if 2 <= len(p) <= 40:
                    g._add(p, "analyte", None)
                for inner in re.findall(r"\(([^)]{2,12})\)", part):
                    g._add(inner, "analyte", None)
    cat = d / "lab_catalogue.csv"
    if cat.is_file():
        for r in _read(cat):
            test, short, panel = r.get("TEST", ""), r.get("Short CODE", ""), r.get("PANEL", "")
            base = re.sub(r"\s*\(.*?\)", "", test)
            row = {"long_name": test, "category": r.get("Department"), "specimen_type": r.get("Sample TYPE")}
            g._add(base, "catalogue", row)
            g._add(re.sub(r"^s\.\s*", "", base, flags=re.I), "catalogue", row)
            g._add(short, "catalogue", row)
            if any(w in panel.lower() for w in _PANEL_WORDS):
                g._add(panel, "catalogue", {"long_name": panel, "category": r.get("Department"), "specimen_type": r.get("Sample TYPE")})
    g.finish()
    return g


def load(data_dir: str | None = None) -> Gazetteer:
    key = data_dir or ""
    with _lock:
        if key not in _cache:
            _cache[key] = build(Path(data_dir) if data_dir else None)
        return _cache[key]


def lookup(text: str | None) -> Match | None:
    return load().lookup(text)
