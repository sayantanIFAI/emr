"""Many written names -> one standard test (the mapping table). The seed answers when there is no database."""
from __future__ import annotations

import pytest

from cdi_adapter.extract import lab_mapping as M
from cdi_adapter.extract import lab_resolve


@pytest.mark.parametrize("written", ["creatinine", "Creatine", "sr creatinine", "Sr. Creatinine", "S.Creatinine", "serum creatine",
                                     "S. Creat", "RFT", "rft", "Renal function test", "KFT"])
def test_every_way_of_writing_creatinine_gives_the_one_test(written):
    m = M.lookup(written)
    assert m is not None and m.canonical == "Creatinine" and m.loinc == "2160-0"


def test_the_gate_uses_the_table_first_and_a_row_without_a_code_is_still_a_recognised_test():
    rz = lab_resolve.resolve("Sr. Creatinine")
    assert rz is not None and rz.long_name == "Creatinine" and rz.source == "mapping" and rz.loinc == "2160-0" and rz.status == "bound"
    pp = lab_resolve.resolve("PPBS")
    assert pp is not None and pp.long_name == "Post-prandial blood sugar" and pp.loinc is None and pp.status is None


def test_urine_creatinine_is_a_different_test_and_is_not_cut_to_creatinine():
    assert M.lookup("urine creatinine") is None


def test_unknown_names_are_not_mapped():
    assert M.lookup("I Revet su") is None and M.lookup("") is None and M.lookup(None) is None


def test_upsert_rejects_empty_and_overlong_names():
    class S:
        def execute(self, *a, **k): raise AssertionError("must not reach the database")
    with pytest.raises(ValueError):
        M.upsert(S(), "", "Creatinine", None, None)
    with pytest.raises(ValueError):
        M.upsert(S(), "x" * 90, "Creatinine", None, None)
