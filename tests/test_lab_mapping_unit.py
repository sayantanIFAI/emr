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


@pytest.mark.parametrize("written,canonical", [
    ("CBC", "Complete blood count"), ("Complete blood picture", "Complete blood count"), ("hemogram", "Complete blood count"),
    ("CRP", "C-reactive protein"), ("C-Reactive Protein", "C-reactive protein"), ("S. CRP", "C-reactive protein"),
    ("LFT", "Liver function test"), ("Liver Function Tests", "Liver function test"),
    ("FT4", "Free T4"), ("FT3", "Free T3"), ("ESR", "ESR"), ("Hb", "Haemoglobin"), ("SGPT", "ALT (SGPT)"), ("S. Lipase", "Lipase"),
    ("Vit B12", "Vitamin B12"), ("Lipid profile", "Lipid profile"), ("Urine R/E", "Urine routine examination"), ("D-dimer", "D-dimer"),
    ("S. Fructosamine", "Fructosamine"),
])
def test_the_common_tests_and_their_spellings_map_to_one_standard_test(written, canonical):
    m = M.lookup(written)
    assert m is not None and m.canonical == canonical


def test_a_code_is_given_only_where_it_is_certain_and_every_alias_is_listed_once():
    codes = {(r[1], r[2]) for r in M.SEED if r[2]}
    assert ("Complete blood count", "58410-2") in codes and ("C-reactive protein", "1988-5") in codes and ("Liver function test", "24325-3") in codes
    assert M.lookup("Lipid profile").loinc is None and M.lookup("Vitamin D").loinc is None            # not certain: name only
    keys = [M.norm(r[0]) for r in M.SEED]
    assert len(keys) == len(set(keys)), "an alias appears twice"
    per_canonical = {}
    for r in M.SEED:
        per_canonical.setdefault(r[1], set()).add(r[2])
    assert all(len(v) == 1 for v in per_canonical.values()), "one standard test, one code"
