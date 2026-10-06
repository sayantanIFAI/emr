"""Handwriting benchmark: Qwen alone, TrOCR alone, or both with the disagreement rule (RD-S2).

Runs three set-ups on the same labelled line crops and reports, for each:

* ``A`` Qwen alone, ``B`` TrOCR alone: every line is accepted as read, so precision is exact match;
* ``C`` both with the production rule (``recognition/disagreement.compare_engines``): a line is
  ACCEPTED only when the two readers agree; anything else goes to a person.

Reported per set-up: character error rate (CER) and word error rate (WER), exact-match rate,
critical-value accuracy (every number on the line exactly right, judged on the literal digits) and
seconds per line. For C: coverage, how many accepted values are wrong, precision with a Wilson lower
bound, and how many wrong readings the disagreement rule caught. The report names the checkpoints
used (the engine versions), so a number is never separated from the model that produced it.

``decide`` applies the RD-S2 decision rule: keep TrOCR only if C beats A on the lower bound of
accepted-value precision, or on coverage at equal precision, by an agreed margin, and the extra time
stays within its limit. The margins are ASSUMPTIONS for the owner to set; they are arguments, they are
printed with the answer, and nothing here was run on real handwriting by whoever wrote it.

Run (needs the pod: TrOCR host + Qwen gateway)::

    python -m cdi_adapter.recognition.bench_htr --lines lines.jsonl --out report.json

``lines.jsonl``: one ``{"id": ..., "crop": "path/to/line.png", "truth": "..."}`` per line, of
DE-IDENTIFIED lines only.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .disagreement import AGREE, compare_engines
from .engines import Reading

Reader = Callable[[list[bytes]], list[Reading]]


@dataclass
class Line:
    id: str
    crop: bytes
    truth: str


# ------------------------------------------------------------------ error rates


def levenshtein(a: str | list[str], b: str | list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        cur = [i]
        for j, y in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().casefold())


def numbers(s: str) -> list[str]:
    """The literal numbers on a line (no look-alike repair: a misread digit is a wrong number)."""
    return re.findall(r"\d+(?:\.\d+)?", s)


def wilson_lower(correct: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the 95 % Wilson interval for a proportion (0.0 for no data)."""
    if n <= 0:
        return 0.0
    p = correct / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)


def score(truths: list[str], preds: list[str]) -> dict[str, Any]:
    """CER / WER over the whole set (total edits / total reference length), exact match and
    critical-value accuracy (lines that carry numbers: all numbers right)."""
    n = len(truths)
    ch_edits = sum(levenshtein(_norm(t), _norm(p)) for t, p in zip(truths, preds))
    ch_len = sum(len(_norm(t)) for t in truths)
    wd_edits = sum(levenshtein(_norm(t).split(), _norm(p).split()) for t, p in zip(truths, preds))
    wd_len = sum(len(_norm(t).split()) for t in truths)
    exact = sum(_norm(t) == _norm(p) for t, p in zip(truths, preds))
    with_numbers = [(t, p) for t, p in zip(truths, preds) if numbers(t)]
    crit = sum(numbers(t) == numbers(p) for t, p in with_numbers)
    return {"lines": n, "cer": round(ch_edits / ch_len, 4) if ch_len else None,
            "wer": round(wd_edits / wd_len, 4) if wd_len else None,
            "exact_match": round(exact / n, 4) if n else None,
            "lines_with_numbers": len(with_numbers),
            "critical_value_accuracy": round(crit / len(with_numbers), 4) if with_numbers else None}


# ------------------------------------------------------------------ the three set-ups


def run_benchmark(lines: list[Line], trocr: Reader, qwen: Reader) -> dict[str, Any]:
    crops = [ln.crop for ln in lines]
    truths = [ln.truth for ln in lines]
    t0 = time.perf_counter()
    tro = trocr(crops)
    t_tro = time.perf_counter() - t0
    t0 = time.perf_counter()
    qwe = qwen(crops)
    t_qwe = time.perf_counter() - t0
    n = len(lines)
    per_line = {"trocr": t_tro / n if n else None, "qwen": t_qwe / n if n else None}

    def text(r: Reading) -> str:
        return r.text if r.ok else ""

    a = score(truths, [text(r) for r in qwe])
    b = score(truths, [text(r) for r in tro])
    a_ok = sum(_norm(t) == _norm(text(r)) for t, r in zip(truths, qwe))
    b_ok = sum(_norm(t) == _norm(text(r)) for t, r in zip(truths, tro))

    states, accepted_pred, accepted_truth, caught = [], [], [], 0
    wrong_if_unchecked = 0
    for t, rt, rq in zip(truths, tro, qwe):
        v = compare_engines([rt, rq])
        states.append(v.state)
        if v.state == AGREE:
            accepted_pred.append(v.display_text)
            accepted_truth.append(t)
        elif _norm(t) != _norm(text(rt)) or _norm(t) != _norm(text(rq)):
            caught += 1                      # a doubtful line went to a person and at least one reader was wrong
        if _norm(t) != _norm(text(rt)) and _norm(t) != _norm(text(rq)):
            wrong_if_unchecked += 1
    acc_wrong = sum(_norm(t) != _norm(p) for t, p in zip(accepted_truth, accepted_pred))
    acc_n = len(accepted_pred)
    c = {**score(accepted_truth, accepted_pred), "accepted": acc_n, "coverage": round(acc_n / n, 4) if n else None,
         "accepted_wrong": acc_wrong,
         "precision": round((acc_n - acc_wrong) / acc_n, 4) if acc_n else None,
         "precision_lower_bound": round(wilson_lower(acc_n - acc_wrong, acc_n), 4),
         "sent_to_review": n - acc_n, "caught_by_disagreement": caught,
         "wrong_by_both_readers": wrong_if_unchecked,
         "states": {s: states.count(s) for s in sorted(set(states))}}
    return {
        "lines": n,
        "checkpoints": {"trocr": sorted({r.engine_version for r in tro}), "qwen": sorted({r.engine_version for r in qwe})},
        "seconds_per_line": {k: None if v is None else round(v, 3) for k, v in per_line.items()},
        "A_qwen_alone": {**a, "accepted": n, "coverage": 1.0 if n else None, "precision": round(a_ok / n, 4) if n else None,
                         "precision_lower_bound": round(wilson_lower(a_ok, n), 4)},
        "B_trocr_alone": {**b, "accepted": n, "coverage": 1.0 if n else None, "precision": round(b_ok / n, 4) if n else None,
                          "precision_lower_bound": round(wilson_lower(b_ok, n), 4)},
        "C_both_with_disagreement_rule": c,
    }


def decide(report: dict[str, Any], *, precision_margin: float, coverage_margin: float,
           max_extra_seconds_per_line: float, equal_precision_eps: float = 0.005) -> dict[str, Any]:
    """The RD-S2 decision rule, fixed before the run. The margins are ASSUMPTIONS for the owner."""
    a, c = report["A_qwen_alone"], report["C_both_with_disagreement_rule"]
    extra = report["seconds_per_line"]["trocr"]
    thresholds = {"precision_margin": precision_margin, "coverage_margin": coverage_margin,
                  "max_extra_seconds_per_line": max_extra_seconds_per_line,
                  "equal_precision_eps": equal_precision_eps, "status": "ASSUMPTION: set by the owner"}
    if not report["lines"] or c["precision"] is None:
        return {"keep_trocr": None, "reason": "no accepted values to compare: run on more lines", "thresholds": thresholds}
    gain = c["precision_lower_bound"] - a["precision_lower_bound"]
    equal = abs(c["precision"] - a["precision"]) <= equal_precision_eps
    wins_precision = gain >= precision_margin
    wins_coverage = equal and (c["coverage"] - a["coverage"]) >= coverage_margin
    within_cost = extra is not None and extra <= max_extra_seconds_per_line
    keep = (wins_precision or wins_coverage) and within_cost
    if not (wins_precision or wins_coverage):
        why = "C does not beat Qwen alone on precision (lower bound) or on coverage at equal precision by the margin"
    elif not within_cost:
        why = f"C wins but TrOCR costs {extra} s per line, above the limit {max_extra_seconds_per_line}"
    else:
        why = "C beats Qwen alone by the agreed margin within the time limit" + (
            " (precision lower bound)" if wins_precision else " (coverage at equal precision)")
    return {"keep_trocr": keep, "reason": why, "precision_lower_bound_gain": round(gain, 4),
            "trocr_seconds_per_line": extra, "thresholds": thresholds,
            "plan_b_if_dropped": "Qwen alone, read twice with different padding (RD-S3), pixel checks, closed lists "
                                 "and number rules; coverage is lower and the review queue longer"}


# ------------------------------------------------------------------ command line


def load_lines(path: Path) -> list[Line]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        crop = (path.parent / row["crop"]).read_bytes()
        out.append(Line(str(row["id"]), crop, str(row["truth"])))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="handwriting benchmark: Qwen alone / TrOCR alone / both")
    ap.add_argument("--lines", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--precision-margin", type=float, default=0.01, help="ASSUMPTION: owner sets")
    ap.add_argument("--coverage-margin", type=float, default=0.02, help="ASSUMPTION: owner sets")
    ap.add_argument("--max-extra-seconds", type=float, default=5.0, help="ASSUMPTION: owner sets")
    args = ap.parse_args()
    from .engines import QwenLineEngine
    from .ocrhost_client import get_ocr_host

    lines = load_lines(args.lines)
    if not lines:
        raise SystemExit("no lines in " + str(args.lines))
    report = run_benchmark(lines, get_ocr_host().trocr, QwenLineEngine().recognize)
    report["decision"] = decide(report, precision_margin=args.precision_margin, coverage_margin=args.coverage_margin,
                                max_extra_seconds_per_line=args.max_extra_seconds)
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
