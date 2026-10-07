"""The scoreboard (ENT-S3): score ``result.v1`` JSON against an answer key and decide pass / fail on thresholds.

An **answer key** is one JSON object per document (JSONL)::

    {"id": "k0001", "image": "k0001.png",
     "slice": {"source": "printed", "kind": "typical"},          # any labels; the scoreboard is cut by each
     "expected": {"patient": {"name": "Ravi Kumar", "age_text": "45 y", "sex": "M", "phone": "9830011234",
                              "dob": null, "address": null},
                  "doctor":  {"name": "Dr A Sen", "reg_no": "12345", "department": "Medicine", "designation": null,
                              "qualification": "MD", "clinic": {"name": "City Care Clinic", "address": null, "phone": null}},
                  "lab_tests": ["HbA1c", "FBS"],
                  "preparation": [{"text": "fasting 12 hrs", "applies_to": ["FBS"]}],
                  "context": [{"test": "FBS", "text": "T2DM"}],
                  "follow_up": {"kind": "interval", "interval_value": 2, "interval_unit": "weeks"},
                  "advice": ["low salt diet"], "medications": ["Metformin"]}}

What is measured (a number is never separated from its sample size):

* **accepted-value precision** = of the values the system ACCEPTED (status ``checked`` / ``accepted``), how many are
  right, with the Wilson 95 % LOWER bound: the number that can be claimed, not the lucky point estimate;
* **coverage** = accepted values / values that are written on the page;
* **flag catch rate** = of the wrong or missing values, how many were flagged ``needs_check`` instead of accepted;
* **context error rate** = of the ``same_line`` diagnosis links the system claimed, how many are not in the key
  (``same_page`` links are not claims and are not counted as errors);
* **made-up preparation** = preparation notes in the result that the key does not have;
* every one of those cut by each slice label, and by field family.

Nothing here is a threshold: the pass/fail numbers live in a thresholds file the owner and a clinician approve.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..recognition.bench_htr import wilson_lower

ACCEPTED = {"checked", "accepted"}
SCALARS = [("patient", k) for k in ("name", "age_text", "dob", "sex", "mrn", "phone", "address", "abha_id")] + \
          [("doctor", k) for k in ("name", "reg_no", "department", "designation", "qualification")] + \
          [("doctor.clinic", k) for k in ("name", "address", "phone")]


def norm(v: Any) -> str:
    """Comparison form: case, spacing and punctuation do not make a value wrong; digits and letters must match."""
    return re.sub(r"[^0-9a-z]+", "", str(v).lower()) if v is not None else ""


def _same(a: Any, b: Any) -> bool:
    """Equal as numbers when both are numbers (2 == 2.0), else by ``norm``."""
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return norm(a) == norm(b)


def _get(res: dict[str, Any], section: str, key: str) -> dict[str, Any]:
    node: Any = res
    for part in section.split("."):
        node = (node or {}).get(part, {})
    return (node or {}).get(key) or {"value": None, "status": "absent"}


class Tally:
    """Counts for one cut of the data."""

    def __init__(self) -> None:
        self.expected = self.accepted = self.accepted_right = self.accepted_wrong = 0
        self.wrong_or_missing = self.flagged_of_those = 0
        self.spurious_accepted = 0                      # a value written nowhere on the page, accepted anyway

    def add(self, expected: Any, got: dict[str, Any]) -> str:
        has = expected is not None
        value, status = got.get("value"), got.get("status")
        ok_status = status in ACCEPTED
        right = has and value is not None and norm(value) == norm(expected)
        if has:
            self.expected += 1
        if ok_status and value is not None:
            self.accepted += 1
            if right:
                self.accepted_right += 1
            else:
                self.accepted_wrong += 1
                if not has:
                    self.spurious_accepted += 1
        if has and not right or (not has and value is not None):
            self.wrong_or_missing += 1
            if status == "needs_check":                  # a silent miss ("absent") is NOT a catch
                self.flagged_of_those += 1
        return "right" if right else ("wrong" if value is not None else "missing")

    def merge(self, o: Tally) -> None:
        for k in vars(self):
            setattr(self, k, getattr(self, k) + getattr(o, k))

    def report(self) -> dict[str, Any]:
        lb = wilson_lower(self.accepted_right, self.accepted) if self.accepted else None
        return {
            "expected_values": self.expected, "accepted_values": self.accepted,
            "accepted_right": self.accepted_right, "accepted_wrong": self.accepted_wrong,
            "accepted_precision": round(self.accepted_right / self.accepted, 4) if self.accepted else None,
            "accepted_precision_lower_bound": None if lb is None else round(lb, 4),
            "coverage": round(self.accepted_right / self.expected, 4) if self.expected else None,
            "wrong_or_missing": self.wrong_or_missing,
            "flag_catch_rate": round(self.flagged_of_those / self.wrong_or_missing, 4) if self.wrong_or_missing else None,
            "spurious_accepted": self.spurious_accepted,
        }


def _list_prf(expected: Iterable[str], got: list[dict[str, Any]], field: str = "as_written") -> dict[str, int]:
    exp = {norm(x) for x in expected if norm(x)}
    found = {norm(g.get(field) or g.get("text")): g for g in got if norm(g.get(field) or g.get("text"))}
    hit = exp & found.keys()
    extra_accepted = [k for k, g in found.items() if k not in exp and g.get("status") == "accepted"]
    return {"expected": len(exp), "found": len(found), "hit": len(hit), "missed": len(exp - found.keys()),
            "extra": len(found.keys() - exp), "extra_accepted": len(extra_accepted)}


def score_document(key: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    exp = key["expected"]
    out: dict[str, Any] = {"id": key["id"], "slice": key.get("slice", {}), "status": res.get("status"), "fields": {}}
    scal = Tally()
    for section, k in SCALARS:
        top, _, sub = section.partition(".")
        want = (exp.get(top, {}).get(sub, {}) if sub else exp.get(top, {})).get(k)
        got = _get(res, section, k)
        outcome = scal.add(want, got)
        out["fields"][f"{section}.{k}"] = {"expected": want, "got": got.get("value"), "status": got.get("status"), "outcome": outcome}
    out["scalar"] = scal
    out["lab_tests"] = _list_prf(exp.get("lab_tests", []), res.get("lab_tests", []))
    out["advice"] = _list_prf(exp.get("advice", []), res.get("advice", []))
    out["medications"] = _list_prf(exp.get("medications", []), res.get("medications", []), "drug")
    exp_prep = {norm(p["text"]) for p in exp.get("preparation", [])}
    got_prep = res.get("lab_preparation", [])
    gp = {norm(p["text"]) for p in got_prep}
    out["preparation"] = {"expected": len(exp_prep), "hit": len(exp_prep & gp), "made_up": len(gp - exp_prep),
                          "made_up_accepted": sum(1 for p in got_prep if norm(p["text"]) not in exp_prep and p["status"] == "checked"),
                          "retracted_by_system": len(res.get("retracted_preparation", []))}
    claimed = {(norm(t["as_written"]), norm(c["text"])) for t in res.get("lab_tests", []) for c in t.get("context", [])
               if c.get("relation") == "same_line"}
    truth = {(norm(c["test"]), norm(c["text"])) for c in exp.get("context", [])}
    out["context"] = {"claimed": len(claimed), "wrong": len(claimed - truth), "expected": len(truth), "found": len(claimed & truth)}
    fu_exp, fu = exp.get("follow_up"), res.get("follow_up", {})
    if fu_exp:
        same = all(_same(fu.get(k), fu_exp.get(k)) for k in ("kind", "interval_value", "interval_unit") if k in fu_exp)
        out["follow_up"] = {"expected": True, "right": same, "accepted": fu.get("status") in ACCEPTED}
    else:
        out["follow_up"] = {"expected": False, "right": fu.get("value") is None, "accepted": fu.get("status") in ACCEPTED and fu.get("value") is not None}
    out["injection_flagged"] = bool(res.get("flags"))
    out["refused"] = bool(res.get("refusal", {}).get("refused"))
    out["expected_refusal"] = bool(key.get("expected_refusal"))
    return out


def scoreboard(per_doc: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate every document score into the overall board and one board per slice label."""
    def board(docs: list[dict[str, Any]]) -> dict[str, Any]:
        t = Tally()
        for d in docs:
            t.merge(d["scalar"])
        agg = lambda sec, keys: {k: sum(d[sec][k] for d in docs) for k in keys}    # noqa: E731
        prep = agg("preparation", ("expected", "hit", "made_up", "made_up_accepted", "retracted_by_system"))
        ctx = agg("context", ("claimed", "wrong", "expected", "found"))
        fu_n = sum(1 for d in docs if d["follow_up"]["expected"])
        fu_ok = sum(1 for d in docs if d["follow_up"]["expected"] and d["follow_up"]["right"])
        fu_spur = sum(1 for d in docs if not d["follow_up"]["expected"] and d["follow_up"]["accepted"])
        return {
            "documents": len(docs), "scalars": t.report(),
            "lab_tests": agg("lab_tests", ("expected", "hit", "missed", "extra", "extra_accepted")),
            "advice": agg("advice", ("expected", "hit", "missed", "extra", "extra_accepted")),
            "medications": agg("medications", ("expected", "hit", "missed", "extra", "extra_accepted")),
            "preparation": prep,
            "context": {**ctx, "error_rate": round(ctx["wrong"] / ctx["claimed"], 4) if ctx["claimed"] else None,
                        "error_rate_upper_bound": None if not ctx["claimed"] else round(1 - wilson_lower(ctx["claimed"] - ctx["wrong"], ctx["claimed"]), 4)},
            "follow_up": {"expected": fu_n, "right": fu_ok, "spurious_accepted": fu_spur},
            "injection_flagged": sum(1 for d in docs if d["injection_flagged"]),
            "refused": sum(1 for d in docs if d["refused"]),
            "refusal_expected": sum(1 for d in docs if d["expected_refusal"]),
            "refusal_missed": sum(1 for d in docs if d["expected_refusal"] and not d["refused"]),
            "refused_but_readable": sum(1 for d in docs if d["refused"] and not d["expected_refusal"]),
        }

    out = {"overall": board(per_doc), "slices": {}}
    labels: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for d in per_doc:
        for lab, val in (d["slice"] or {}).items():
            labels[lab][str(val)].append(d)
    for lab, groups in labels.items():
        out["slices"][lab] = {val: board(docs) for val, docs in sorted(groups.items())}
    return out


# ------------------------------------------------------------------ the gate
def gate(board: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    """Reasons the release is blocked (empty = pass). Thresholds are PLACEHOLDERS until the owner and a clinician set them."""
    bad: list[str] = []
    o = board["overall"]
    s = o["scalars"]
    if o["documents"] < thresholds["min_documents"]:
        bad.append(f"only {o['documents']} documents scored; at least {thresholds['min_documents']} are required for a claim")
    lb = s["accepted_precision_lower_bound"]
    if lb is None or lb < thresholds["min_accepted_precision_lower_bound"]:
        bad.append(f"accepted-value precision lower bound {lb} is below {thresholds['min_accepted_precision_lower_bound']}")
    if (s["coverage"] or 0) < thresholds["min_coverage"]:
        bad.append(f"coverage {s['coverage']} is below {thresholds['min_coverage']}")
    ce = o["context"]["error_rate_upper_bound"]
    if ce is not None and ce > thresholds["max_context_error_rate_upper_bound"]:
        bad.append(f"context error rate upper bound {ce} is above {thresholds['max_context_error_rate_upper_bound']}")
    if o["refusal_missed"]:
        bad.append(f"{o['refusal_missed']} unreadable document(s) were read instead of being sent back for a retake")
    if o["preparation"]["made_up_accepted"] > thresholds["max_made_up_preparation_accepted"]:
        bad.append(f"{o['preparation']['made_up_accepted']} made-up preparation notes were accepted")
    for lab, groups in board["slices"].items():
        for val, b in groups.items():
            if lab == "kind" and val == "adversarial":
                if b["scalars"]["spurious_accepted"] or b["preparation"]["made_up_accepted"] or b["follow_up"]["spurious_accepted"]:
                    bad.append("an adversarial document got a value accepted that is not on the page")
                continue
            slb = b["scalars"]["accepted_precision_lower_bound"]
            if b["scalars"]["accepted_values"] >= thresholds["min_slice_accepted_values"] and slb is not None \
                    and slb < thresholds["min_slice_precision_lower_bound"]:
                bad.append(f"slice {lab}={val}: precision lower bound {slb} is below {thresholds['min_slice_precision_lower_bound']}")
    return bad


def load_key(path: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def run(key: list[dict[str, Any]], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    per_doc = [score_document(k, results[k["id"]]) for k in key if k["id"] in results]
    board = scoreboard(per_doc)
    board["missing_results"] = sorted(k["id"] for k in key if k["id"] not in results)
    return board


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="score result.v1 JSON files against an answer key")
    ap.add_argument("--key", required=True, type=Path)
    ap.add_argument("--results", required=True, type=Path, help="folder of <id>.json (result.v1)")
    ap.add_argument("--thresholds", type=Path, help="thresholds JSON: exit 1 when the gate fails")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    key = load_key(a.key)
    results = {p.stem.split(".")[0]: json.loads(p.read_text(encoding="utf-8")) for p in a.results.glob("*.json")}
    board = run(key, results)
    if a.out:
        a.out.write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(board["overall"], indent=2))
    if a.thresholds:
        bad = gate(board, json.loads(a.thresholds.read_text("utf-8")))
        for b in bad:
            print("GATE FAILED:", b)
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
