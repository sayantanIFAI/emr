"""Patients on the upload screen are a (mobile number, name) pair; a name read a little differently each time is the same patient."""
from __future__ import annotations

import pytest

from cdi_adapter.webapp import patients as P


@pytest.mark.parametrize("a,b", [("Onkar Broadway", "Oukar Broadway"), ("Mr. Onkar Broadway", "Onkar Broadway"), ("Supta Choudhury", "Mrs Supta Choudhury"),
                                 ("Ramesh Kumar", "ramesh  kumar"), (None, ""), ("", None)])
def test_alike_names_on_one_number_are_one_patient(a, b):
    assert P.same_patient(a, b)


@pytest.mark.parametrize("a,b", [("Ramesh Kumar", "Suresh Das"), ("Asha Rao", "Asha Rani Singh"), ("Ramesh Kumar", None), (None, "Ramesh Kumar")])
def test_different_names_are_different_patients(a, b):
    assert not P.same_patient(a, b)


def test_rows_of_one_number_with_alike_names_merge_and_the_most_read_name_is_shown():
    rows = [{"phone": "9830011234", "name": "Oukar Broadway", "prescriptions": 1, "last_uploaded": "2026-10-07T16:53:00"},
            {"phone": "9830011234", "name": "Onkar Broadway", "prescriptions": 3, "last_uploaded": "2026-10-07T16:51:00"},
            {"phone": "9830011234", "name": "Asha Rao", "prescriptions": 1, "last_uploaded": "2026-10-01T10:00:00"},
            {"phone": "9000000001", "name": "Onkar Broadway", "prescriptions": 1, "last_uploaded": "2026-10-02T10:00:00"}]
    got = P._cluster(rows)
    assert [(g["phone"], g["name"], g["prescriptions"]) for g in got] == [
        ("9830011234", "Onkar Broadway", 4), ("9000000001", "Onkar Broadway", 1), ("9830011234", "Asha Rao", 1)]
    assert got[0]["last_uploaded"] == "2026-10-07T16:53:00"                    # the same name on another number stays separate
