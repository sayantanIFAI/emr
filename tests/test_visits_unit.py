"""Which dated entry is the latest, across the pages and the entries below a ruled line (extract/visits.py)."""
from __future__ import annotations

from cdi_adapter.extract import visits as V


def _page(date, tests, fu=None, earlier=None, name=None, clinic=None):
    p = {"patient": {"name": name}, "prescriber": {"name": None, "clinic": clinic or {}}, "encounter_date": date,
         "investigations": [{"text": t, "evidence": ["b1"]} for t in tests], "follow_up": fu, "advice": [], "diagnoses": []}
    if earlier is not None:
        p["earlier_entries"] = earlier
    return p


def test_the_back_page_with_a_later_date_gives_the_tests_and_the_booking():
    front = _page("12/03/2026", ["HbA1c"], "review after 1 month", name="Asha Rao")
    back = _page("20/05/2026", ["Creatinine", "TSH"], "review after 2 wks")
    m = V.merge([front, back])
    assert [i["text"] for i in m["investigations"]] == ["Creatinine", "TSH"] and m["follow_up"] == "review after 2 wks"
    assert m["encounter_date"] == "20/05/2026" and m["_latest_page"] == 2 and m["patient"]["name"] == "Asha Rao"
    assert [v["page"] for v in m["visits"]] == [2, 1] and [v["is_latest"] for v in m["visits"]] == [True, False]
    assert m["visits"][0]["date"] == "2026-05-20" and m["visits"][1]["lab_tests"] == ["HbA1c"]


def test_a_later_entry_below_the_ruled_line_beats_the_top_entry():
    p = _page("01/02/2026", ["CBC"], "review after 1 wk",
              earlier=[{"date_text": "15/04/2026", "investigations": ["LFT", "KFT"], "follow_up": "after 1 month"}])
    m = V.merge([p])
    assert [i for i in m["investigations"]] == ["LFT", "KFT"] and m["follow_up"] == "after 1 month"
    assert m["visits"][0]["where"] == "page 1, another dated entry" and m["visits"][0]["is_latest"] and "earlier_entries" not in m


def test_no_dates_anywhere_means_the_front_page_is_current_and_an_undated_entry_never_outranks_a_dated_one():
    m = V.merge([_page(None, ["CBC"], "x"), _page(None, ["LFT"], "y")])
    assert [i["text"] for i in m["investigations"]] == ["CBC"] and m["_latest_page"] == 1
    m2 = V.merge([_page("05/01/2026", ["CBC"]), _page(None, ["LFT"])])
    assert [i["text"] for i in m2["investigations"]] == ["CBC"] and m2["visits"][-1]["date"] is None


def test_a_single_plain_page_adds_no_visits_list_unless_it_has_a_date():
    assert "visits" not in V.merge([_page(None, ["CBC"])])
    assert [v["date"] for v in V.merge([_page("05/01/2026", ["CBC"])])["visits"]] == ["2026-01-05"]


def test_identity_and_the_organisation_are_taken_from_whichever_page_says_them():
    a = _page("01/01/2026", [], name=None, clinic={"name": "District Hospital, Bankura", "address": None})
    b = _page("02/01/2026", [], name="Ravi Das", clinic={"name": None, "address": "Bankura, WB"})
    m = V.merge([a, b])
    assert m["patient"]["name"] == "Ravi Das" and m["prescriber"]["clinic"] == {"name": "District Hospital, Bankura", "address": "Bankura, WB"}


def test_dates_in_the_three_written_forms_are_compared_as_dates_not_text():
    m = V.merge([_page("9/12/2025", ["A"]), _page("02-Jan-2026", ["B"]), _page("2025-12-30", ["C"])])
    assert [i["text"] for i in m["investigations"]] == ["B"]
