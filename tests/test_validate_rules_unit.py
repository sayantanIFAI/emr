"""Unit tests for the S6 deterministic rules + calibration. No infra."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cdi_adapter.validate import rules as R
from cdi_adapter.validate.service import _calibrate


def _lab(code, val, unit="%"):
    return {"fact_type": "lab_result", "code": code, "code_display": "x",
            "local_text": "x", "value_num": val, "value_unit_ucum": unit}


def test_value_range_ok():
    assert R.check_value_range(_lab("4548-4", 7.8)) == []


def test_value_range_blocker():
    fs = R.check_value_range(_lab("4548-4", 250.0))
    assert any(s == "blocker" and c == "value-out-of-range" for s, c, _m in fs)


def test_temp_fahrenheit_normalized_not_flagged():
    fs = R.check_value_range({"fact_type": "vital_sign", "code": "8310-5",
                              "code_display": "temp", "local_text": "temp",
                              "value_num": 98.4, "value_unit_ucum": "[degF]"})
    assert any(c == "unit-normalized" for _s, c, _m in fs)
    assert not any(s == "blocker" for s, _c, _m in fs)


def test_medication_missing_dose_and_freq_blocks():
    fs = R.check_medication({"fact_type": "medication", "local_text": "Metformin", "code": "372567009"},
                            {"drug_text": "Metformin"})
    codes = {c for _s, c, _m in fs}
    assert "med-no-dose" in codes and "med-no-frequency" in codes


def test_medication_dose_ceiling():
    fs = R.check_medication(
        {"fact_type": "medication", "local_text": "Metformin", "code": "372567009"},
        {"drug_text": "Metformin", "dose_num": 2000, "dose_unit_ucum": "mg", "frequency_per_day": 3},
    )
    assert any(c == "med-dose-exceeds-ceiling" for _s, c, _m in fs)


def test_future_date_blocks():
    f = {"fact_type": "lab_result", "effective_time": datetime.now(timezone.utc) + timedelta(days=30)}
    assert any(c == "date-in-future" for _s, c, _m in R.check_dates(f, None))


def test_evidence_missing_blocks():
    assert any(s == "blocker" for s, _c, _m in R.check_evidence({}, []))


def test_evidence_present_ok():
    assert R.check_evidence({}, [{"ocr_block_ids": ["x"], "extracted_text": "HbA1c"}]) == []


def test_calibrate_partial_penalty_and_caps():
    assert _calibrate(0.95, partial=True, n_warn=0, n_block=0) == 0.65
    assert _calibrate(0.99, partial=False, n_warn=0, n_block=1) == 0.5
    assert _calibrate(0.99, partial=False, n_warn=1, n_block=0) == 0.9
    assert _calibrate(0.99, partial=False, n_warn=0, n_block=0) == 0.99
