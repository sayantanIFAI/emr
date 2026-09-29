"""Doctor master matching (E18 / E6-S11): link a document to a ``cn_practitioner`` row.

Evidence, strongest first:
  registration_no   'Reg. No. 12345' read in the header == master registration_number
  printed_name      header / prescriber name vs master name + aliases (Jaro-Winkler)
Specialty / designation words in the header break name ties (two 'Dr. A. Sen's).

The result always carries the extracted text AND the master name, so the reviewer sees
"read: Dr A Sen  ->  DB: Dr. A. Sen (General Medicine, reg 12345), 0.97". Below
``practitioner_min_link_conf`` the document is NOT linked; the candidates are kept as
evidence for a human to pick.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from ..config import settings

_TITLES = re.compile(r"\b(dr|doctor|prof|mr|mrs|ms)\b\.?", re.I)
_REG = re.compile(r"(?:reg(?:istration)?\.?\s*(?:no|number|#)?\.?\s*[:\-]?\s*)([A-Z]{0,4}[-/ ]?\d{3,7})", re.I)
_DEGREES = re.compile(r"\b(mbbs|md|ms|dnb|frcs|mrcp|dm|mch|dgo|dch|do|bds|mds)\b.*$", re.I)


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    rng = max(la, lb) // 2 - 1
    am, bm = [False] * la, [False] * lb
    m = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - rng), min(lb, i + rng + 1)):
            if not bm[j] and b[j] == ch:
                am[i] = bm[j] = True
                m += 1
                break
    if not m:
        return 0.0
    t, k = 0, 0
    for i in range(la):
        if am[i]:
            while not bm[k]:
                k += 1
            if a[i] != b[k]:
                t += 1
            k += 1
    jaro = (m / la + m / lb + (m - t / 2) / m) / 3
    p = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        p += 1
    return jaro + p * 0.1 * (1 - jaro)


def norm_name(s: str | None) -> str:
    t = _DEGREES.sub("", s or "")
    t = re.sub(r"\(.*?\)", " ", t)
    t = _TITLES.sub(" ", t)
    t = re.sub(r"[^a-z ]", " ", t.casefold())
    return re.sub(r"\s+", " ", t).strip()


def name_score(read: str, master: str) -> float:
    """Initial-aware: 'a sen' vs 'arindam sen' scores on surname + compatible initial."""
    a, b = norm_name(read).split(), norm_name(master).split()
    if not a or not b:
        return 0.0
    full = jaro_winkler(" ".join(a), " ".join(b))
    sur = jaro_winkler(a[-1], b[-1])
    ga, gb = a[:-1], b[:-1]
    if ga and gb:
        g = 1.0 if (len(ga[0]) == 1 or len(gb[0]) == 1) and ga[0][0] == gb[0][0] \
            else jaro_winkler(ga[0], gb[0])
        # an initial only matches its first letter - it cannot be as strong as a full name
        if len(ga[0]) == 1 or len(gb[0]) == 1:
            g = min(g, 0.9)
    else:
        g = 0.8
    return round(max(full, 0.6 * sur + 0.4 * g) if sur >= 0.85 else full * 0.8, 4)


def find_reg_numbers(texts: list[str]) -> list[str]:
    out = []
    for t in texts:
        for m in _REG.finditer(t or ""):
            out.append(re.sub(r"[^0-9A-Za-z]", "", m.group(1)).upper())
    return out


@dataclass
class PractitionerMatch:
    practitioner_id: str | None
    method: str                         # registration_no | printed_name | unknown
    confidence: float
    extracted_name: str | None
    extracted_reg: str | None
    db_name: str | None
    linked: bool
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def evidence(self) -> dict[str, Any]:
        return {"extracted_name": self.extracted_name, "extracted_reg": self.extracted_reg,
                "db_name": self.db_name, "method": self.method, "confidence": self.confidence,
                "linked": self.linked, "candidates": self.candidates}


def match(masters: list[dict[str, Any]], *, name: str | None, reg_no: str | None,
          header_texts: list[str]) -> PractitionerMatch:
    regs = [re.sub(r"[^0-9A-Za-z]", "", reg_no).upper()] if reg_no else []
    regs += find_reg_numbers(header_texts)
    hdr = " ".join(header_texts).casefold()
    scored: list[dict[str, Any]] = []
    for p in masters:
        pr = re.sub(r"[^0-9A-Za-z]", "", p.get("registration_number") or "").upper()
        reg_hit = bool(pr) and pr in regs
        names = [p.get("name_full") or ""] + list(p.get("name_aliases") or [])
        ns = max((name_score(name, n) for n in names), default=0.0) if name else 0.0
        if not name:     # no prescriber field: look for the name in the header lines
            ns = max((name_score(t, n) for t in header_texts for n in names), default=0.0) * 0.95
        spec_words = [w.casefold() for w in (list(p.get("specialty") or [])
                                             + [p.get("department") or "", p.get("designation") or ""]) if w]
        spec_hit = any(w and w in hdr for w in spec_words)
        if reg_hit:
            conf = 0.99 if ns >= 0.75 or not name else 0.9   # reg agrees, name does not: review
            method = "registration_no"
        else:
            conf = ns * (1.0 if spec_hit else 0.97)
            method = "printed_name"
        scored.append({"practitioner_id": str(p["id"]), "db_name": p.get("name_full"),
                       "registration_number": p.get("registration_number"),
                       "specialty": p.get("department"), "designation": p.get("designation"),
                       "score": round(conf, 4), "method": method, "reg_hit": reg_hit,
                       "name_score": ns, "specialty_hit": spec_hit})
    scored.sort(key=lambda c: -c["score"])
    top = scored[:5]
    if not top or top[0]["score"] < 0.5:
        return PractitionerMatch(None, "unknown", 0.0, name, regs[0] if regs else None,
                                 None, False, top)
    best = top[0]
    conf = best["score"]
    # two masters nearly tied on name alone (e.g. two Dr Sens) -> never auto-link
    if len(top) > 1 and not best["reg_hit"] and best["score"] - top[1]["score"] < 0.03:
        conf = min(conf, settings.practitioner_min_link_conf - 0.01)
    # an initial ('A. Sen') that fits more than one master is ambiguous however exact
    # the alias match is - only a registration number can settle it
    if not best["reg_hit"] and name and _initial_ambiguous(name, masters):
        conf = min(conf, settings.practitioner_min_link_conf - 0.01)
    linked = conf >= settings.practitioner_min_link_conf
    return PractitionerMatch(best["practitioner_id"] if linked else None, best["method"],
                             round(conf, 4), name, regs[0] if regs else None,
                             best["db_name"], linked, top)


def _initial_ambiguous(name: str, masters: list[dict[str, Any]]) -> bool:
    toks = norm_name(name).split()
    if len(toks) < 2 or len(toks[0]) != 1:
        return False
    ini, sur = toks[0], toks[-1]
    fits = 0
    for p in masters:
        mt = norm_name(p.get("name_full")).split()
        if len(mt) >= 2 and jaro_winkler(mt[-1], sur) >= 0.92 and mt[0].startswith(ini):
            fits += 1
    return fits > 1


def load_masters(sess: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in sess.execute(text(
        "SELECT id, registration_number, name_full, name_aliases, specialty, department, "
        "designation FROM cn_practitioner WHERE coalesce(license_status,'active') = 'active' "
        "AND registration_number IS NOT NULL")).mappings()]


def link_document(sess: Any, document_id: str, *, name: str | None, reg_no: str | None,
                  header_texts: list[str]) -> PractitionerMatch:
    m = match(load_masters(sess), name=name, reg_no=reg_no, header_texts=header_texts)
    sess.execute(text(
        "UPDATE source_document SET practitioner_id = :p, practitioner_link_method = :m, "
        "practitioner_link_confidence = :c, practitioner_evidence = CAST(:e AS jsonb) "
        "WHERE id = :d"),
        {"p": m.practitioner_id, "m": m.method if m.linked else "unknown",
         "c": m.confidence, "e": __import__("json").dumps(m.evidence(), default=str),
         "d": document_id})
    return m
