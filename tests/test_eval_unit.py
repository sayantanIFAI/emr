"""ENT-S3 scoreboard + SW-S4 contest: the arithmetic, the gate and the verdict rules. No model, no database."""
from __future__ import annotations

import copy
import json
import random

import pytest

import test_json_connector_unit as T
from cdi_adapter.compliance import models as M
from cdi_adapter.eval import contest, scorer, synthetic
from cdi_adapter.output import json_connector as jc


def _key(**over):
    exp = {"patient": {"name": "Anil Mehra", "age_text": "54 y", "sex": "M", "phone": "9830011234", "dob": None, "address": None},
           "doctor": {"name": "Dr A Sen", "reg_no": "12345", "department": "Medicine", "designation": None, "qualification": "MD",
                      "clinic": {"name": "City Care Clinic", "address": None, "phone": "98300 11234"}},
           "lab_tests": ["HbA1c", "FBS"], "preparation": [{"text": "fasting 12 hrs", "applies_to": ["FBS"]}],
           "context": [], "follow_up": {"kind": "interval", "interval_value": 2, "interval_unit": "weeks"},
           "advice": ["low salt diet"], "medications": ["Metformin"]}
    return {"id": "k1", "slice": {"source": "printed", "kind": "typical"}, "expected": exp, **over}


def _result(**kw):
    return jc.build_result(T._inputs([T.HBA, T.FBS, T.ADV, T.MED], payload=T.PAYLOAD, **kw))


def test_a_correct_result_scores_clean():
    d = scorer.score_document(_key(), _result())
    s = d["scalar"].report()
    assert s["accepted_right"] == 10 and s["accepted_wrong"] == 0 and s["accepted_precision"] == 1.0
    assert d["lab_tests"]["hit"] == 2 and d["preparation"]["hit"] == 1 and d["preparation"]["made_up"] == 0
    assert d["follow_up"] == {"expected": True, "right": True, "accepted": True}


def test_normalisation_ignores_case_spacing_and_punctuation_but_not_digits_or_letters():
    assert scorer.norm("Dr. A. Sen") == scorer.norm("dr a sen") and scorer.norm("98300 11234") == "9830011234"
    assert scorer.norm("12345") != scorer.norm("12346") and scorer.norm("Sen") != scorer.norm("Sem")


def test_a_wrong_accepted_value_is_counted_and_lowers_precision():
    k = _key()
    k["expected"]["doctor"]["reg_no"] = "99999"                      # the page says something else
    s = scorer.score_document(k, _result())["scalar"].report()
    assert s["accepted_wrong"] == 1 and s["accepted_precision"] < 1.0 and s["accepted_precision_lower_bound"] < s["accepted_precision"]


def test_a_value_the_system_flagged_is_a_catch_not_an_accepted_error():
    k = _key()
    k["expected"]["patient"]["phone"] = "9999999999"
    res = _result()
    res["patient"]["phone"]["status"] = "needs_check"
    s = scorer.score_document(k, res)["scalar"].report()
    assert s["flag_catch_rate"] == 1.0 and s["accepted_wrong"] == 0 and s["wrong_or_missing"] == 1


def test_a_silent_miss_is_not_a_catch():
    k = _key()
    res = _result()
    res["patient"]["name"] = {"value": None, "status": "absent", "reason": None, "confidence": None}
    s = scorer.score_document(k, res)["scalar"].report()
    assert s["wrong_or_missing"] == 1 and s["flag_catch_rate"] == 0.0


def test_a_value_invented_for_an_empty_field_is_spurious_and_counted():
    k = _key()
    k["expected"]["patient"]["name"] = None                         # nothing is written for the name
    s = scorer.score_document(k, _result())["scalar"].report()
    assert s["spurious_accepted"] == 1


def test_made_up_preparation_and_wrong_context_are_counted():
    res = _result()
    res["lab_preparation"].append({"type": "fasting", "value": 8.0, "unit": "h", "text": "fasting 8 hrs", "applies_to": ["all"],
                                   "status": "checked", "reason": None, "evidence": []})
    res["lab_tests"][0]["context"] = [{"text": "Dengue", "kind": "diagnosis", "relation": "same_line", "quote": "x"},
                                      {"text": "T2DM", "kind": "diagnosis", "relation": "same_page", "quote": None}]
    d = scorer.score_document(_key(), res)
    assert d["preparation"]["made_up"] == 1 and d["preparation"]["made_up_accepted"] == 1
    assert d["context"] == {"claimed": 1, "wrong": 1, "expected": 0, "found": 0}        # same_page is not a claim


def test_the_scoreboard_cuts_by_every_slice_label_and_reports_sample_sizes():
    d1 = scorer.score_document(_key(), _result())
    k2 = _key(id="k2", slice={"source": "photo", "kind": "typical"})
    d2 = scorer.score_document(k2, _result())
    b = scorer.scoreboard([d1, d2])
    assert b["overall"]["documents"] == 2 and set(b["slices"]) == {"source", "kind"}
    assert b["slices"]["source"]["printed"]["documents"] == 1 and b["slices"]["source"]["photo"]["scalars"]["accepted_values"] == 10


def test_the_gate_blocks_a_small_sample_and_passes_a_big_clean_one():
    th = json.loads((scorer.Path(scorer.__file__).parent / "thresholds.json").read_text())
    one = scorer.scoreboard([scorer.score_document(_key(), _result())])
    assert any("only 1 documents scored" in b for b in scorer.gate(one, th))
    docs = [scorer.score_document(_key(id=f"k{i}"), _result()) for i in range(320)]
    big = scorer.scoreboard(docs)
    assert scorer.gate(big, th) == [], scorer.gate(big, th)
    wrong = copy.deepcopy(docs)
    for d in wrong[:150]:
        d["scalar"].accepted_right -= 1
        d["scalar"].accepted_wrong += 1
    assert any("precision lower bound" in b for b in scorer.gate(scorer.scoreboard(wrong), th))


def test_an_adversarial_document_that_gets_an_invented_value_accepted_blocks_the_release():
    th = json.loads((scorer.Path(scorer.__file__).parent / "thresholds.json").read_text())
    th["min_documents"] = 1
    k = _key(slice={"source": "synthetic", "kind": "adversarial"})
    k["expected"]["patient"]["name"] = None
    board = scorer.scoreboard([scorer.score_document(k, _result())])
    assert any("adversarial" in b for b in scorer.gate(board, th))


def test_an_unreadable_picture_must_be_sent_back_not_read():
    th = json.loads((scorer.Path(scorer.__file__).parent / "thresholds.json").read_text())
    th["min_documents"] = 1
    k = _key(expected_refusal=True, slice={"kind": "blurred"})
    board = scorer.scoreboard([scorer.score_document(k, _result())])                      # it WAS read
    assert board["overall"]["refusal_missed"] == 1 and any("sent back for a retake" in b for b in scorer.gate(board, th))
    held = jc.build_result(T._inputs([], status="quality_hold", doc_extra={"error_detail": "rescan"}))
    assert scorer.scoreboard([scorer.score_document(k, held)])["overall"]["refusal_missed"] == 0


# ------------------------------------------------------------------ the synthetic key
def test_the_synthetic_key_is_deterministic_and_has_every_kind(tmp_path):
    a = synthetic.build(60, 3, tmp_path / "a")
    b = synthetic.build(60, 3, tmp_path / "b")
    assert a == b and (tmp_path / "a" / a[0]["image"]).read_bytes() == (tmp_path / "b" / b[0]["image"]).read_bytes()
    assert {k["slice"]["kind"] for k in a} == {"typical", "minimal", "adversarial", "blurred"}
    k = next(k for k in a if k["slice"]["kind"] == "minimal")
    assert k["expected"]["patient"]["phone"] is None and k["expected"]["doctor"]["clinic"]["name"] is None     # absent stays absent
    assert next(k for k in a if k["slice"]["kind"] == "blurred")["expected_refusal"] is True
    assert scorer.load_key(tmp_path / "a" / "key.jsonl") == a


def test_the_synthetic_ground_truth_matches_what_is_printed():
    r = random.Random(1)
    lines, exp = synthetic.make_case(r, 0, "typical")
    text = "\n".join(lines)
    for t in exp["lab_tests"]:
        assert t in text
    assert exp["patient"]["name"] in text and exp["doctor"]["reg_no"] in text and exp["patient"]["phone"] in text
    assert all(p["text"] in text for p in exp["preparation"])


# ------------------------------------------------------------------ the contest
def _board(precision_right, accepted, **slices):
    d = scorer.score_document(_key(), _result())
    d["scalar"].accepted, d["scalar"].accepted_right, d["scalar"].accepted_wrong = accepted, precision_right, accepted - precision_right
    d["scalar"].expected = accepted
    docs = []
    for lab, groups in slices.items():
        for val, (right, n) in groups.items():
            x = copy.deepcopy(d)
            x["slice"] = {lab: val}
            x["scalar"].accepted, x["scalar"].accepted_right, x["scalar"].accepted_wrong = n, right, n - right
            docs.append(x)
    return scorer.scoreboard(docs)


RULES = {"max_slice_precision_drop": 0.02, "min_slice_accepted_values": 30, "min_accepted_values": 300, "max_seconds_ratio": 1.5,
         "max_cost_per_document": None, "approved_by": "po+clinician", "approved_on": "2026-10-06"}
PERF = {"seconds_per_document_mean": 30.0}


def test_a_better_candidate_with_no_slice_drop_is_promoted():
    champ = _board(0, 0, source={"printed": (480, 500), "photo": (440, 500)})
    cand = _board(0, 0, source={"printed": (495, 500), "photo": (470, 500)})
    v = contest.compare(cand, champ, RULES, cand_perf=PERF, champ_perf=PERF)
    assert v["verdict"] == "promote" and v["wins"] == 2 and v["losses"] == 0


def test_a_candidate_better_overall_but_with_one_slice_dropping_keeps_the_champion_and_names_the_slice():     # AC2
    champ = _board(0, 0, source={"printed": (480, 500), "photo": (470, 500)})
    cand = _board(0, 0, source={"printed": (499, 500), "photo": (440, 500)})
    v = contest.compare(cand, champ, RULES, cand_perf=PERF, champ_perf=PERF)
    assert v["verdict"] == "keep champion" and any("slice source=photo drops" in r for r in v["reasons"])


def test_too_small_a_sample_says_need_more_data():                                                          # AC3
    champ = _board(0, 0, source={"printed": (95, 100)})
    cand = _board(0, 0, source={"printed": (99, 100)})
    v = contest.compare(cand, champ, RULES, cand_perf=PERF, champ_perf=PERF)
    assert v["verdict"] == "need more data" and "300 are needed" in v["reasons"][0]


def test_speed_and_cost_limits_are_rules_too():
    champ = _board(0, 0, source={"printed": (480, 500)})
    cand = _board(0, 0, source={"printed": (495, 500)})
    slow = contest.compare(cand, champ, RULES, cand_perf={"seconds_per_document_mean": 60.0}, champ_perf=PERF)
    assert slow["verdict"] == "keep champion" and "2.0x slower" in slow["reasons"][0]
    dear = contest.compare(cand, champ, {**RULES, "max_cost_per_document": 0.001}, cand_perf=PERF, champ_perf=PERF, rent_per_hour=1.2)
    assert dear["verdict"] == "keep champion" and dear["cost_per_document"] == 0.01 and "above the limit" in dear["reasons"][0]


def test_a_lower_precision_bound_than_the_champion_is_never_promoted():
    champ = _board(0, 0, source={"printed": (495, 500)})
    cand = _board(0, 0, source={"printed": (485, 500)})
    assert contest.compare(cand, champ, RULES, cand_perf=PERF, champ_perf=PERF)["verdict"] == "keep champion"


def test_a_candidate_with_no_licence_entry_cannot_start_a_contest():                                         # AC4
    with pytest.raises(contest.ContestRefused, match="not registered"):
        contest.check_start("Someone/Unlicensed-VL", RULES)
    reg = copy.deepcopy(M.load_registry())
    reg["models"][0]["licence"]["text_file"] = "licences/missing.txt"
    # a registered model whose licence text is missing is refused the same way
    import unittest.mock as mock
    with mock.patch.object(M, "load_registry", lambda path=None: reg), pytest.raises(contest.ContestRefused, match="licence text"):
        contest.check_start(reg["models"][0]["model_id"], RULES)


def test_the_margins_must_be_approved_before_a_verdict_counts():
    unapproved = {**RULES, "approved_by": None, "approved_on": None}
    with pytest.raises(contest.ContestRefused, match="not approved"):
        contest.check_start("Qwen/Qwen2.5-VL-7B-Instruct", unapproved)
    contest.check_start("Qwen/Qwen2.5-VL-7B-Instruct", unapproved, allow_unapproved=True)                   # a dry run is allowed
    shipped = contest.load_rules()
    assert shipped["approved_by"] is None                                                                  # nobody approved them yet: say so


def test_the_report_has_both_models_on_the_same_slices(tmp_path):                                           # AC1
    key = [dict(_key(id=f"k{i}")) for i in range(3)]
    (tmp_path / "key.jsonl").write_text("\n".join(json.dumps(k) for k in key))
    for d in ("cand", "champ"):
        (tmp_path / d).mkdir()
        for k in key:
            (tmp_path / d / f"{k['id']}.json").write_text(json.dumps(_result()))
        (tmp_path / d / "_timing.json").write_text(json.dumps({"seconds_per_document_mean": 30.0}))
    rep = contest.run_contest(candidate_id="Qwen/Qwen2.5-VL-7B-Instruct", champion_id="Qwen/Qwen2.5-VL-7B-Instruct",
                              key_path=tmp_path / "key.jsonl", cand_dir=tmp_path / "cand", champ_dir=tmp_path / "champ",
                              allow_unapproved=True, save=False)
    assert rep["candidate_scalars"]["accepted_values"] == rep["champion_scalars"]["accepted_values"]
    assert set(rep["slices"]) == {"source=printed", "kind=typical"} and rep["speed"]["ratio"] == 1.0
    assert rep["verdict_note"].startswith("UNAPPROVED RULES") and rep["candidate_gpu_memory_gb"]["value"] == 17.1
