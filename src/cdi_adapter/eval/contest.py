"""A fair contest: a candidate model against the champion (SW-S4).

Same answer key, same guard documents, same machine, same scoreboard; the verdict comes from rules written down
and APPROVED before the run (``contest_rules.json``: ``approved_by`` / ``approved_on`` must be filled by the
product owner and a clinician, or the contest refuses to give a verdict). The verdict is one of:

* ``promote``: nothing drops more than the margin, the candidate's lower bound of accepted-value precision is not
  below the champion's, speed and cost are within their limits, the licence check passes, and the sample is big enough;
* ``keep champion``: a rule failed (the reasons name the slice or the limit);
* ``need more data``: the sample is too small to prove the precision claim either way.

The numbers in ``contest_rules.json`` are PLACEHOLDERS (an ASSUMPTION of the engineers) until approved.
Both runs are made by pointing ``eval.run_live`` at the same machine with the champion and then the candidate
(``CDI_VLM_MODEL_ID`` + a gateway restart), so the contest compares result folders, never live services.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..compliance import models as M
from . import scorer

RULES_PATH = Path(__file__).resolve().parent / "contest_rules.json"
REPORT_DIR = Path(__file__).resolve().parents[1] / "compliance" / "contests"


class ContestRefused(RuntimeError):
    pass


def load_rules(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or RULES_PATH).read_text(encoding="utf-8"))


def check_start(candidate_id: str, rules: dict[str, Any], *, allow_unapproved: bool = False) -> None:
    """Refuse to start: a candidate with no licence entry (SW-S4 AC4), or margins nobody approved."""
    try:
        M.check_model(candidate_id, "contest candidate", require_champion=False)
    except M.ModelRefused as exc:
        raise ContestRefused(f"the contest will not start: {exc}") from exc
    if not allow_unapproved and not (rules.get("approved_by") and rules.get("approved_on")):
        raise ContestRefused("the contest rules are not approved yet (contest_rules.json needs approved_by and approved_on "
                             "from the product owner and a clinician); margins are fixed BEFORE the first run")


def _slice_lb(b: dict[str, Any]) -> float | None:
    return b["scalars"]["accepted_precision_lower_bound"]


def compare(candidate: dict[str, Any], champion: dict[str, Any], rules: dict[str, Any], *,
            cand_perf: dict[str, Any], champ_perf: dict[str, Any], rent_per_hour: float | None = None) -> dict[str, Any]:
    """Pure: two scoreboards (scorer.scoreboard output) and two timing summaries in, a verdict out."""
    reasons: list[str] = []
    co, ho = candidate["overall"], champion["overall"]
    # a slice-by-slice table: wins / ties / losses on the lower bound of accepted-value precision
    wins = ties = losses = 0
    table: dict[str, Any] = {}
    margin = rules["max_slice_precision_drop"]
    for lab, groups in champion["slices"].items():
        for val, hb in groups.items():
            cb = candidate["slices"].get(lab, {}).get(val)
            if cb is None:
                continue
            c_lb, h_lb = _slice_lb(cb), _slice_lb(hb)
            n_c, n_h = cb["scalars"]["accepted_values"], hb["scalars"]["accepted_values"]
            if c_lb is None or h_lb is None:
                verdict = "no data"
            elif c_lb > h_lb + 1e-9:
                verdict, wins = "win", wins + 1
            elif abs(c_lb - h_lb) <= 1e-9:
                verdict, ties = "tie", ties + 1
            else:
                verdict, losses = "loss", losses + 1
            table[f"{lab}={val}"] = {"candidate_lb": c_lb, "champion_lb": h_lb, "candidate_n": n_c, "champion_n": n_h, "result": verdict}
            if c_lb is not None and h_lb is not None and n_c >= rules["min_slice_accepted_values"] and h_lb - c_lb > margin:
                reasons.append(f"slice {lab}={val} drops {round(h_lb - c_lb, 4)} (allowed {margin}): candidate {c_lb} vs champion {h_lb}")
    needs_more = []
    for who, o in (("candidate", co), ("champion", ho)):
        if o["scalars"]["accepted_values"] < rules["min_accepted_values"]:
            needs_more.append(f"{who} has {o['scalars']['accepted_values']} accepted values; {rules['min_accepted_values']} are needed")
    c_lb, h_lb = _slice_lb(co), _slice_lb(ho)
    if c_lb is not None and h_lb is not None and c_lb < h_lb:
        reasons.append(f"candidate precision lower bound {c_lb} is below the champion's {h_lb}")
    s_ratio = None
    if cand_perf.get("seconds_per_document_mean") and champ_perf.get("seconds_per_document_mean"):
        s_ratio = round(cand_perf["seconds_per_document_mean"] / champ_perf["seconds_per_document_mean"], 3)
        if s_ratio > rules["max_seconds_ratio"]:
            reasons.append(f"candidate is {s_ratio}x slower (limit {rules['max_seconds_ratio']}x)")
    cost = None
    if rent_per_hour is not None and cand_perf.get("seconds_per_document_mean"):
        cost = round(cand_perf["seconds_per_document_mean"] * rent_per_hour / 3600, 5)
        if rules.get("max_cost_per_document") is not None and cost > rules["max_cost_per_document"]:
            reasons.append(f"cost {cost} per document is above the limit {rules['max_cost_per_document']}")
    if reasons:
        verdict = "keep champion"
    elif needs_more:
        verdict, reasons = "need more data", needs_more
    else:
        verdict = "promote"
    return {"verdict": verdict, "reasons": reasons, "wins": wins, "ties": ties, "losses": losses, "slices": table,
            "candidate_scalars": co["scalars"], "champion_scalars": ho["scalars"],
            "speed": {"candidate_s": cand_perf.get("seconds_per_document_mean"), "champion_s": champ_perf.get("seconds_per_document_mean"), "ratio": s_ratio},
            "cost_per_document": cost, "gpu_memory_gb": {"note": "read from the registry entry of each model", },
            "rules": {k: v for k, v in rules.items() if not k.startswith("_")}}


def run_contest(*, candidate_id: str, champion_id: str, key_path: Path, cand_dir: Path, champ_dir: Path,
                rules_path: Path | None = None, rent_per_hour: float | None = None, allow_unapproved: bool = False,
                save: bool = True) -> dict[str, Any]:
    rules = load_rules(rules_path)
    check_start(candidate_id, rules, allow_unapproved=allow_unapproved)
    key = scorer.load_key(key_path)

    def board(d: Path) -> dict[str, Any]:
        res = {p.stem.split(".")[0]: json.loads(p.read_text("utf-8")) for p in d.glob("*.json") if not p.name.startswith("_")}
        return scorer.run(key, res)

    def perf(d: Path) -> dict[str, Any]:
        p = d / "_timing.json"
        return json.loads(p.read_text("utf-8")) if p.exists() else {}

    report = compare(board(cand_dir), board(champ_dir), rules, cand_perf=perf(cand_dir), champ_perf=perf(champ_dir), rent_per_hour=rent_per_hour)
    report.update(candidate_model=candidate_id, champion_model=champion_id, rules_approved=bool(rules.get("approved_by") and rules.get("approved_on")),
                  candidate_gpu_memory_gb=(M.entry(candidate_id) or {}).get("gpu_memory_gb"),
                  champion_gpu_memory_gb=(M.entry(champion_id) or {}).get("gpu_memory_gb"))
    if not report["rules_approved"]:
        report["verdict_note"] = "UNAPPROVED RULES: this verdict is a dry run, not a decision"
    if save:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"{candidate_id.replace('/', '--')}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="candidate vs champion on the same answer key")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--champion", required=True)
    ap.add_argument("--key", required=True, type=Path)
    ap.add_argument("--candidate-results", required=True, type=Path)
    ap.add_argument("--champion-results", required=True, type=Path)
    ap.add_argument("--rent-per-hour", type=float)
    ap.add_argument("--allow-unapproved", action="store_true", help="dry run with unapproved rules (the report says so)")
    a = ap.parse_args(argv)
    try:
        rep = run_contest(candidate_id=a.candidate, champion_id=a.champion, key_path=a.key, cand_dir=a.candidate_results,
                          champ_dir=a.champion_results, rent_per_hour=a.rent_per_hour, allow_unapproved=a.allow_unapproved)
    except ContestRefused as exc:
        print("REFUSED:", exc)
        return 2
    print(json.dumps({k: rep[k] for k in ("verdict", "reasons", "wins", "ties", "losses", "speed")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
