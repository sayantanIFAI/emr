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


def test_consensus_picks_the_name_most_readings_agree_on():
    from cdi_adapter.names import consensus
    assert consensus(["Mr. Oukar Broadway", "Oukar Chowdury", "Oukar Chowdhury", "Oukar Chowdury"]) == ("Oukar Chowdury", 3, 4)
    got, n, total = consensus(["Asha Rao", "Ravi Das", "Kiran Paul"])
    assert (n, total) == (1, 3) and got == "Asha Rao"                      # no agreement: one each, the first reading is shown
    assert consensus([None, "", "  "]) == (None, 0, 0)


def test_a_reading_that_goes_on_where_the_shown_one_stops_is_the_better_suggestion():
    from cdi_adapter.names import prefer_complete
    reads = ["Smita Gupta", "Smita Gupta Gangopadhyay", "Smith Gupta"]
    assert prefer_complete("Smita Gupta", reads) == "Smita Gupta Gangopadhyay"
    assert prefer_complete("Mr. Smita Gupta", reads) == "Smita Gupta Gangopadhyay"            # a title does not matter
    assert prefer_complete("Smita Gupta Gangopadhyay", reads) == "Smita Gupta Gangopadhyay"   # never shortened
    assert prefer_complete("Ravi Das", reads) == "Ravi Das"                                   # unrelated readings do not extend it
    assert prefer_complete("Asha", ["Asha Rao Kumar Singh Verma"]) == "Asha"                  # not more than two extra words
    assert prefer_complete(None, reads) is None
