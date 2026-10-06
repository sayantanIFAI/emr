"""Handwriting benchmark harness (RD-S2): the arithmetic and the decision rule, with FAKE readers.

Nothing here measures a real reader; it proves the harness counts correctly so that a run on real,
de-identified handwriting can be trusted.
"""
from __future__ import annotations

import json

import pytest

from cdi_adapter.recognition import bench_htr as bh
from cdi_adapter.recognition.engines import Reading

TRUTHS = [f"Tab Telma {40 + i} 1-0-1" for i in range(100)]


def lines(truths=TRUTHS):
    return [bh.Line(f"l{i}", f"crop{i}".encode(), t) for i, t in enumerate(truths)]


def reader(engine, version, wrong: set[int] | None = None, fixed: str | None = None, error_on: set[int] = frozenset()):
    """A scripted reader: right on every line except ``wrong`` (indexes), where it reads text off by a digit."""
    wrong = wrong or set()

    def read(crops):
        out = []
        for c in crops:
            i = int(c.decode().removeprefix("crop"))
            if i in error_on:
                out.append(Reading(engine, version, "", None, error="host down"))
            elif i in wrong:
                out.append(Reading(engine, version, TRUTHS[i].replace("1-0-1", "1-0-0"), None))
            else:
                out.append(Reading(engine, version, fixed or TRUTHS[i], 0.9))
        return out

    return read


# ------------------------------------------------------------------ the arithmetic


def test_levenshtein_and_error_rates():
    assert bh.levenshtein("kitten", "sitting") == 3 and bh.levenshtein("", "abc") == 3
    assert bh.levenshtein(["a", "b", "c"], ["a", "x", "c", "d"]) == 2
    s = bh.score(["telma 40", "hba1c 7.8"], ["telma 40", "hba1c 7.9"])
    assert s["exact_match"] == 0.5 and s["wer"] == round(1 / 4, 4) and s["cer"] == round(1 / 17, 4)


def test_critical_value_accuracy_judges_the_literal_digits():
    s = bh.score(["Telma 40 1-0-1", "no numbers here", "HbA1c 7.8"], ["Telma 4O 1-0-1", "no numbers here", "HbA1c 7.8"])
    assert s["lines_with_numbers"] == 2 and s["critical_value_accuracy"] == 0.5       # 4O is not 40


def test_case_and_spacing_do_not_count_as_errors():
    assert bh.score(["Telma  40"], ["telma 40"])["exact_match"] == 1.0


def test_wilson_lower_bound():
    assert bh.wilson_lower(0, 0) == 0.0
    assert bh.wilson_lower(100, 100) == pytest.approx(0.963, abs=0.002)
    assert bh.wilson_lower(95, 100) < 0.95 < bh.wilson_lower(950, 1000) + 0.02
    assert bh.wilson_lower(950, 1000) > bh.wilson_lower(95, 100)                  # more data, tighter bound


# ------------------------------------------------------------------ the three set-ups


def test_when_both_readers_are_right_everything_is_accepted():
    r = bh.run_benchmark(lines(), reader("trocr", "trocr-base-handwritten"), reader("qwen2.5-vl", "Qwen2.5-VL-7B"))
    c = r["C_both_with_disagreement_rule"]
    assert (c["accepted"], c["coverage"], c["precision"], c["accepted_wrong"]) == (100, 1.0, 1.0, 0)
    assert r["A_qwen_alone"]["exact_match"] == r["B_trocr_alone"]["exact_match"] == 1.0
    assert r["checkpoints"] == {"trocr": ["trocr-base-handwritten"], "qwen": ["Qwen2.5-VL-7B"]}    # named in the report


def test_disagreement_sends_doubtful_lines_to_review_and_raises_precision():
    qwen_wrong = set(range(0, 100, 5))                      # Qwen is wrong on 20 lines; TrOCR is right on all
    r = bh.run_benchmark(lines(), reader("trocr", "t"), reader("qwen2.5-vl", "q", wrong=qwen_wrong))
    a, c = r["A_qwen_alone"], r["C_both_with_disagreement_rule"]
    assert a["precision"] == 0.8 and a["coverage"] == 1.0
    assert (c["accepted"], c["sent_to_review"], c["accepted_wrong"], c["precision"]) == (80, 20, 0, 1.0)
    assert c["caught_by_disagreement"] == 20 and c["wrong_by_both_readers"] == 0
    assert c["states"] == {"agree": 80, "disagree": 20}


def test_two_readers_wrong_in_the_same_way_still_reach_accepted_and_are_counted():
    same_wrong = set(range(10))
    r = bh.run_benchmark(lines(), reader("trocr", "t", wrong=same_wrong), reader("qwen2.5-vl", "q", wrong=same_wrong))
    c = r["C_both_with_disagreement_rule"]
    assert c["accepted_wrong"] == 10 and c["wrong_by_both_readers"] == 10 and c["precision"] == round(90 / 100, 4)


def test_a_reader_that_is_down_leaves_one_reading_and_those_lines_go_to_review():
    r = bh.run_benchmark(lines(), reader("trocr", "t", error_on=set(range(30))), reader("qwen2.5-vl", "q"))
    c = r["C_both_with_disagreement_rule"]
    assert c["accepted"] == 70 and c["states"] == {"agree": 70, "single_engine": 30}
    assert r["B_trocr_alone"]["exact_match"] == 0.7                       # a failed read counts as wrong, not as skipped


def test_the_report_has_timings_and_is_plain_json():
    r = bh.run_benchmark(lines(), reader("trocr", "t"), reader("qwen2.5-vl", "q"))
    assert set(r["seconds_per_line"]) == {"trocr", "qwen"}
    json.dumps(r)


# ------------------------------------------------------------------ the decision rule


KW = {"precision_margin": 0.02, "coverage_margin": 0.05, "max_extra_seconds_per_line": 5.0}


def _report(qwen_wrong=frozenset(), trocr_seconds=1.0, n=1000, trocr_wrong=frozenset()):
    global TRUTHS
    truths = [f"Tab Telma {40 + i % 50} 1-0-1 line {i}" for i in range(n)]
    old, TRUTHS = TRUTHS, truths
    try:
        r = bh.run_benchmark(lines(truths), reader("trocr", "t", wrong=set(trocr_wrong)),
                             reader("qwen2.5-vl", "q", wrong=set(qwen_wrong)))
    finally:
        TRUTHS = old
    r["seconds_per_line"]["trocr"] = trocr_seconds
    return r


def test_trocr_is_kept_when_both_beat_qwen_alone_by_the_margin_within_the_time_limit():
    out = bh.decide(_report(qwen_wrong=range(0, 1000, 10)), **KW)               # Qwen wrong on 10 %
    assert out["keep_trocr"] is True and "precision lower bound" in out["reason"]
    assert out["thresholds"]["status"].startswith("ASSUMPTION")


def test_trocr_is_dropped_when_it_adds_nothing():
    out = bh.decide(_report(), **KW)                                           # both perfect: no gain anywhere
    assert out["keep_trocr"] is False and "does not beat" in out["reason"] and "Plan B" not in out["reason"]
    assert "Qwen alone" in out["plan_b_if_dropped"]


def test_trocr_is_dropped_when_it_wins_but_costs_too_much_time():
    out = bh.decide(_report(qwen_wrong=range(0, 1000, 10), trocr_seconds=4.5), **{**KW, "max_extra_seconds_per_line": 2.0})
    assert out["keep_trocr"] is False and "4.5 s per line" in out["reason"]


def test_with_nothing_accepted_there_is_no_decision():
    r = _report(n=20, qwen_wrong=range(20), trocr_wrong=range(0))
    r["C_both_with_disagreement_rule"]["precision"] = None
    assert bh.decide(r, **KW)["keep_trocr"] is None


# ------------------------------------------------------------------ loading a labelled set


def test_lines_are_loaded_from_jsonl_next_to_their_crops(tmp_path):
    (tmp_path / "a.png").write_bytes(b"A")
    (tmp_path / "b.png").write_bytes(b"B")
    (tmp_path / "lines.jsonl").write_text(
        json.dumps({"id": 1, "crop": "a.png", "truth": "Telma 40"}) + "\n\n"
        + json.dumps({"id": "x", "crop": "b.png", "truth": "HbA1c"}) + "\n", encoding="utf-8")
    got = bh.load_lines(tmp_path / "lines.jsonl")
    assert [(g.id, g.crop, g.truth) for g in got] == [("1", b"A", "Telma 40"), ("x", b"B", "HbA1c")]
