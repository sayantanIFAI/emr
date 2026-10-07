"""Is this "test" really a test? A guard on the lab-test list.

A reader that struggles with a crowded handwritten page sometimes files a drug line under the tests the doctor
ordered ("In Candilock (lots)" for "Inj Cardiloc (10/5)"). Nothing may show a medicine as a lab test, so each
entry is checked for the marks of a medicine order: a form prefix (Inj, Tab, Cap, Syp ...), a dose or strength,
a frequency, a duration. A name that is a known test (CBC, KFT, TSH, ECG ...) is never rejected on those marks.
This only ever REMOVES an entry from the tests; it never invents one.
"""
from __future__ import annotations

import difflib
import re

# test names / abbreviations as doctors write them (a word of the entry, case-insensitive)
_TESTS = (
    "cbc", "hb", "hemoglobin", "haemoglobin", "tlc", "dlc", "esr", "crp", "hscrp", "kft", "rft", "lft", "urea",
    "creatinine", "bun", "sodium", "potassium", "electrolytes?", "tsh", "t3", "t4", "ft3", "ft4", "hba1c", "fbs",
    "ppbs", "rbs", "fbg", "ogtt", "glucose", "sugar", "lipids?", "cholesterol", "triglycerides?", "hdl", "ldl",
    "urine", "stool", "culture", "sensitivity", "widal", "dengue", "ns1", "malaria", "mp", "hiv", "hbsag", "hcv",
    "vdrl", "pt", "inr", "aptt", "ptt", "d-?dimer", "troponin", "cpk", "ck-?mb", "bnp", "ecg", "ekg", "eeg", "emg",
    "echo", "echocardiography", "2d", "tmt", "x-?ray", "cxr", "usg", "ultrasound", "sonography", "ct", "mri", "pet",
    "mammogram", "dexa", "bmd", "biopsy", "fnac", "profile", "panel", "serology", "covid", "rt-?pcr", "pcr",
    "antigen", "vitamin", "vit", "b12", "ferritin", "iron", "calcium", "uric", "albumin", "bilirubin", "sgot", "sgpt",
    "ast", "alt", "alp", "ggt", "amylase", "lipase", "psa", "ca-?125", "cea", "afp", "pap", "peripheral", "smear",
    "blood", "r/e", "m/e", "c/s", "ana", "rf", "aso", "hepatitis", "thyroid", "liver", "kidney", "renal",
    "tft", "abg", "pft", "spirometry", "holter", "endoscopy", "colonoscopy", "mantoux", "doppler", "angiography",
    "angiogram", "scan", "mammography", "hemogram", "haemogram", "urinalysis", "sputum", "afb", "cbnaat", "rbc",
    "wbc", "platelets?", "retic", "audiometry", "ncv", "oct", "fundoscopy", "osteo", "bone", "densitometry",
    "glycated", "microalbumin", "acr", "pcr", "igE", "vitamin", "folate", "homocysteine", "lactate", "cortisol",
    "prolactin", "fsh", "lh", "testosterone", "hcg", "beta", "semen", "pap", "hpv", "mp", "cd4", "viral", "load",
)
_TEST = re.compile(r"(?<![a-z0-9])(?:" + "|".join(_TESTS) + r")(?![a-z0-9])", re.IGNORECASE)

_FORM_PREFIX = re.compile(
    r"^\s*(?:inj|in|tab|t|cap|c|syp|sy|syr|susp|oint|cream|gel|lotion|drops?|neb|inh|iv|im|sc|po|rx)\b[\s.:\-]*[a-z]",
    re.IGNORECASE)
_DOSE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|µg|ug|ml|iu|units?|gm)\b", re.IGNORECASE)
_STRENGTH = re.compile(r"\(\s*\d+(?:\.\d+)?\s*(?:/\s*\d+(?:\.\d+)?)?\s*\)")          # (10/5)  (20)
_FREQUENCY = re.compile(
    r"\b(?:od|bd|bid|tds|tid|qid|qds|sos|hs|stat|prn)\b|\b\d\s?-\s?\d\s?-\s?\d\b", re.IGNORECASE)
_DURATION = re.compile(                                  # "x 10d", "× 5 days", "3x10d", "x 1 month", "1t"
    r"[x×]\s?\d+\s?(?:d|day|days|wk|wks|week|weeks|m|mo|month|months)\b|\b\d+\s?[x×]\s?\d+\s?d\b|\b1t\b",
    re.IGNORECASE)
_COUNT = re.compile(r"\b\d+\s?(?:tabs?|caps?|drops?|puffs?|sachets?)\b", re.IGNORECASE)    # "1 tab", "2 caps"


def is_known_test(text: str | None) -> bool:
    """True if a word of the entry is a test name or abbreviation (CBC, KFT, TSH, ECG ...)."""
    return bool(text and _TEST.search(text))


def looks_like_medicine(text: str | None) -> bool:
    """True when the entry carries the marks of a medicine order and is not a recognised test."""
    t = (text or "").strip()
    if not t or _STRONG_RE.search(t):
        return False                     # a name that is only ever a test (CBC, KFT, TSH ...) wins over a number
    marks = (_FORM_PREFIX, _DOSE, _STRENGTH, _FREQUENCY, _DURATION, _COUNT)
    return any(m.search(t) for m in marks)


# --- the other direction: a list of test abbreviations that was filed as a medicine, and a list to split -------

# names that are tests and nothing else (no "iron", "calcium", "vitamin": those are also medicines)
_STRONG = frozenset((
    "cbc hb tlc dlc esr crp kft rft lft tsh t3 t4 ft3 ft4 hba1c fbs ppbs rbs fbg ogtt ecg ekg eeg emg echo tmt xray "
    "x-ray cxr usg ct mri hiv hbsag hcv vdrl pt inr aptt ptt d-dimer ddimer troponin cpk bnp psa widal ns1 urea "
    "creatinine sgot sgpt bilirubin uric hdl ldl lipid profile urine stool culture covid rt-pcr rtpcr pcr").split())
_FILLER = frozenset("and with blood serum urine for test tests routine adv advice inv ix investigation investigations "
                    "f u fu r e m c s".split())


def is_test_list(text: str | None) -> bool:
    """True for an entry made ONLY of test names / abbreviations ("CBC/KFT/LFT", "FBS, HbA1c") and carrying no
    dose, schedule or duration: the shape of a test line on a prescription, whatever field the reader put it in."""
    t = (text or "").strip()
    if not t or looks_like_medicine(t):
        return False
    words = re.findall(r"[a-z0-9+\-]+", t.casefold())
    return bool(words) and any(w in _STRONG for w in words) and all(w in _STRONG or w in _FILLER for w in words)


_LIST_NO = re.compile(r"^\s*(?:\(\s*\d{1,2}\s*\)|\d{1,2}\s*[.)]|\[\s*\d{1,2}\s*\])\s*")


# a result written after the test ("S. Lipase-916", "HbA1c - 7.8", "Fructosamine L-216"): not part of the test's name
_RESULT_TAIL = re.compile(r"(?<=[A-Za-z]{3})\s*[-–:=]\s*\d{3,}(?:\.\d+)?\s*$|(?<=[A-Za-z0-9]{3})\s*[-–:=]\s*\d+\.\d+\s*$"
                          r"|(?<=[A-Za-z]{4})\s+[LlHh]\s*[-–>]\s*\d{2,}(?:\.\d+)?\s*$")
_BRACKETS = re.compile(r"[{}\[\]]")


def split_tests(text: str | None) -> list[str]:
    """One entry per test: ``"CBC/KFT/LFT"`` -> CBC, KFT, LFT; ``"Blood: CBC, Urea, FBS"`` -> CBC, Urea, FBS. A name that
    contains a space or a one-letter part (``"Urine R/E"``, ``"X-ray LS spine"``) is never cut."""
    t = re.sub(r"^\s*(?:blood|serum|urine|adv(?:ice|ised)?|inv(?:estigations?)?|ix)\s*[:\-]\s*", "", (text or "").strip(), flags=re.I)
    if not t:
        return []
    out: list[str] = []
    for part in re.split(r"\s*[,;+]\s*(?=[A-Za-z0-9(\[{])|\s+and\s+", t):
        part = _LIST_NO.sub("", part).strip(" .")             # "(1) CBC" / "2. LFT" / "3) TSH" -> the test only
        part = _BRACKETS.sub("", part).strip(" .")             # "{HbA1c" / "TSH}" / "[HbA1c / FBS]": the braces are the doctor's
        part = _RESULT_TAIL.sub("", part).strip(" .")
        if not part:
            continue
        spaced = [b.strip() for b in re.split(r"\s+/\s+", part)]               # "HbA1c / FBS / PPBS": a list, whatever the names
        if len(spaced) > 1 and all(len(b) >= 2 for b in spaced):
            out += [_RESULT_TAIL.sub("", b).strip(" .") for b in spaced]
            continue
        bits = part.split("/")
        if len(bits) > 1 and " " not in part and all(len(b) >= 2 for b in bits):
            out += [b for b in bits]
        else:
            out.append(part)
    seen: set[str] = set()
    uniq = [x for x in out if x and not (x.casefold() in seen or seen.add(x.casefold()))]
    return uniq or [t]


UNRECOGNISED = "not a recognised test name"      # stable prefix of the reason (UP-S3 / the result screen keys on it)


_STRONG_RE = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(w) for w in sorted(_STRONG)) + r")(?![a-z0-9])", re.IGNORECASE)


UNCONFIRMED = "not confirmed on the page"        # stable prefix of the reason
SECOND_LOOK = "found by a second look at the page, please check it"   # a test read from an enlarged piece of the image

_GENERIC = frozenset("test tests for and of the with blood serum urine function profile scan study screen routine "
                     "panel examination exam general count".split())


def is_grounded(test: str | None, page_text: str) -> bool:
    """True if the test's own words occur in the page's text (misspellings allowed). The model can answer a
    page it cannot read with a plausible list of standard tests; a test that nothing on the page supports must
    not be shown as one the doctor ordered. Words too short or too generic to judge are not held against it."""
    words = re.findall(r"[a-z0-9]+", (test or "").casefold())
    key = [w for w in words if len(w) >= 2 and w not in _GENERIC] or [w for w in words if len(w) >= 2]
    if not key:
        return True                                      # nothing to judge by
    page = set(re.findall(r"[a-z0-9]+", page_text.casefold()))
    pool = [w for w in page if len(w) >= 3]
    hits = 0
    for w in key:
        if w in page or (len(w) >= 4 and difflib.get_close_matches(w, pool, n=1, cutoff=0.8)):
            hits += 1
    return hits / len(key) >= 0.5
