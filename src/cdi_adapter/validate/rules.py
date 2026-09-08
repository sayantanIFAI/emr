"""Deterministic clinical-plausibility rules.

Each rule returns a list of Finding(severity, code, message). Severity:
  blocker  -> fact cannot be auto-accepted; mandatory human review
  warn     -> fact may be auto-accepted at high confidence, but audited
  info     -> recorded, no gate effect
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

Finding = tuple[str, str, str]  # (severity, code, message)

# ---- physiological plausibility by LOINC (absolute min/max) ---------------
LAB_RANGE: dict[str, tuple[float, float, str]] = {
    "4548-4":  (2.0, 20.0, "%"),        # HbA1c
    "1558-6":  (20.0, 800.0, "mg/dL"),  # fasting glucose
    "2345-7":  (20.0, 1500.0, "mg/dL"), # glucose
    "2160-0":  (0.1, 25.0, "mg/dL"),    # creatinine
    "3094-0":  (2.0, 400.0, "mg/dL"),   # urea nitrogen
    "2093-3":  (30.0, 600.0, "mg/dL"),  # total cholesterol
    "2089-1":  (10.0, 500.0, "mg/dL"),  # LDL
    "2085-9":  (5.0, 200.0, "mg/dL"),   # HDL
    "2571-8":  (10.0, 5000.0, "mg/dL"), # triglycerides
    "1742-6":  (1.0, 5000.0, "U/L"),    # ALT
    "1920-8":  (1.0, 5000.0, "U/L"),    # AST
    "3016-3":  (0.001, 500.0, "m[IU]/L"),  # TSH
    "1975-2":  (0.05, 60.0, "mg/dL"),   # total bilirubin
    "718-7":   (2.0, 25.0, "g/dL"),     # hemoglobin
    "777-3":   (2.0, 2000.0, "10*3/uL"),   # platelets
    "6690-2":  (0.1, 200.0, "10*3/uL"), # WBC
}
VITAL_RANGE: dict[str, tuple[float, float, str]] = {
    "8480-6":  (50.0, 300.0, "mm[Hg]"),   # systolic BP
    "8462-4":  (20.0, 200.0, "mm[Hg]"),   # diastolic BP
    "8867-4":  (20.0, 250.0, "/min"),     # heart rate
    "9279-1":  (4.0, 80.0, "/min"),       # resp rate
    "8310-5":  (30.0, 45.0, "Cel"),       # body temp (C)
    "59408-5": (40.0, 100.0, "%"),        # SpO2
    "29463-7": (0.5, 400.0, "kg"),        # weight
    "8302-2":  (20.0, 260.0, "cm"),       # height
    "39156-5": (5.0, 120.0, "kg/m2"),     # BMI
    "72514-3": (0.0, 10.0, "{score}"),    # pain score
}
# adult total-daily-dose ceilings (mg/day) by SNOMED substance code
DOSE_MAX_MG_DAY: dict[str, float] = {
    "372567009": 3000.0,   # metformin
    "386864001": 20.0,     # amlodipine
    "373444002": 80.0,     # atorvastatin
    "395892000": 80.0,     # telmisartan
    "373567002": 100.0,    # losartan
    "395807002": 8.0,      # glimepiride
    "387458008": 4000.0,   # aspirin (antiplatelet doses far lower)
    "387517004": 4000.0,   # paracetamol
    "395728002": 80.0,     # pantoprazole
    "387137007": 80.0,     # omeprazole
    "710809001": 0.5,      # levothyroxine (mg)
}
_TEMP_F = re.compile(r"\bF\b|fahrenheit", re.I)


def _f_to_c(v: float) -> float:
    return (v - 32.0) * 5.0 / 9.0


def check_value_range(f: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    code = f.get("code")
    val = f.get("value_num")
    if val is None or code is None:
        return out
    table = LAB_RANGE if f["fact_type"] == "lab_result" else VITAL_RANGE if f["fact_type"] == "vital_sign" else {}
    rng = table.get(str(code))
    if not rng:
        return out
    lo, hi, want_unit = rng
    v = float(val)
    unit = (f.get("value_unit_ucum") or "").strip()
    # temperature commonly recorded in F
    if str(code) == "8310-5" and (v > 45 or _TEMP_F.search(unit or "")):
        v = _f_to_c(v)
        out.append(("info", "unit-normalized", f"temperature {val}{unit or 'F'} read as {v:.1f} Cel"))
    if v < lo or v > hi:
        out.append(("blocker", "value-out-of-range",
                    f"{f.get('code_display') or f['local_text']} = {val} {unit} outside plausible [{lo}, {hi}] {want_unit}"))
    if unit and want_unit and unit.lower() != want_unit.lower():
        out.append(("warn", "unit-mismatch",
                    f"unit '{unit}' != expected '{want_unit}' for {f.get('code_display') or f['local_text']}"))
    return out


def check_unit_present(f: dict[str, Any]) -> list[Finding]:
    if f["fact_type"] in ("lab_result", "vital_sign") and f.get("value_num") is not None \
            and not (f.get("value_unit_ucum") or "").strip():
        return [("warn", "missing-unit", f"{f['local_text']} has a numeric value but no unit")]
    return []


def check_medication(f: dict[str, Any], md: dict[str, Any] | None) -> list[Finding]:
    out: list[Finding] = []
    md = md or {}
    have_dose = md.get("dose_num") is not None or md.get("strength_num") is not None
    have_freq = md.get("frequency_per_day") is not None or bool(md.get("frequency_code"))
    if not have_dose:
        out.append(("blocker", "med-no-dose", f"{md.get('drug_text') or f['local_text']}: no dose/strength read"))
    if not have_freq:
        out.append(("blocker", "med-no-frequency", f"{md.get('drug_text') or f['local_text']}: no frequency read"))
    fpd = md.get("frequency_per_day")
    if fpd is not None and not (0 < float(fpd) <= 6):
        out.append(("blocker", "med-frequency-implausible", f"frequency/day = {fpd}"))
    # daily dose ceiling
    code = f.get("code")
    dose = md.get("dose_num") or md.get("strength_num")
    if code and dose is not None and fpd:
        unit = (md.get("dose_unit_ucum") or md.get("strength_unit") or "").lower()
        per_day = float(dose) * float(fpd)
        if unit in ("g", "gm"):
            per_day *= 1000.0
        elif unit in ("mcg", "ug", "µg"):
            per_day /= 1000.0
        ceil = DOSE_MAX_MG_DAY.get(str(code))
        if ceil and per_day > ceil * 1.5:
            out.append(("blocker", "med-dose-exceeds-ceiling",
                        f"~{per_day:.0f} mg/day exceeds adult ceiling ~{ceil:.0f} mg/day"))
    return out


def check_dates(f: dict[str, Any], enc: dict[str, Any] | None) -> list[Finding]:
    out: list[Finding] = []
    now = datetime.now(timezone.utc)
    for field in ("effective_time", "onset", "asserted_time"):
        t = f.get(field)
        if not isinstance(t, datetime):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t > now + timedelta(days=1):
            out.append(("blocker", "date-in-future", f"{field} {t.date()} is in the future"))
        if t.year < 1900:
            out.append(("blocker", "date-implausible", f"{field} {t.isoformat()} before 1900"))
        if enc and enc.get("period_start"):
            ps = enc["period_start"]
            if isinstance(ps, datetime):
                ps = ps if ps.tzinfo else ps.replace(tzinfo=timezone.utc)
                if abs((t - ps).days) > 400:
                    out.append(("warn", "date-far-from-encounter",
                                f"{field} {t.date()} is >400d from encounter {ps.date()}"))
    return out


def check_evidence(f: dict[str, Any], prov: list[dict[str, Any]]) -> list[Finding]:
    if not prov:
        return [("blocker", "no-provenance", "fact has no provenance row")]
    spans = any(p.get("ocr_block_ids") or (p.get("extracted_text") or "").strip() for p in prov)
    if not spans:
        return [("warn", "weak-evidence", "provenance has no OCR span or extracted text")]
    return []


def check_terminology(f: dict[str, Any], review_types: tuple[str, ...]) -> list[Finding]:
    if f["fact_type"] in review_types and f.get("code_status") in (None, "unmapped", "local_only"):
        return [("warn", "unmapped-concept",
                 f"{f['fact_type']} '{f['local_text']}' not bound to a standard code")]
    return []
