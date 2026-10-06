"""False-reject / false-accept report for the picture check (IM-S1 AC3).

Run it on a labelled set (the answer-key set once it exists)::

    python -m cdi_adapter.recognition.quality_eval --readable DIR --unreadable DIR [--manifest m.csv] [--json out.json]

``readable``: pictures a person can read, so any hold is a FALSE REJECT. ``unreadable``: pictures a
person cannot read, so any pass is a FALSE ACCEPT. An optional manifest CSV (``path,label,doc_type``)
splits the counts by document type. The report always prints the thresholds it was run with
(``CDI_QUALITY_*``), because a rate means nothing without them. Nothing here is a threshold
recommendation: it only measures.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import settings
from . import quality

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
LABELS = ("readable", "unreadable")


def thresholds() -> dict[str, Any]:
    return {k: getattr(settings, k) for k in sorted(type(settings).model_fields) if k.startswith("quality_")}


def load_items(readable: Path | None, unreadable: Path | None,
               manifest: Path | None) -> list[tuple[Path, str, str]]:
    """``(path, label, doc_type)`` for every picture."""
    items: list[tuple[Path, str, str]] = []
    if manifest:
        base = manifest.parent
        with manifest.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["label"] not in LABELS:
                    raise ValueError(f"{row['path']}: label must be one of {LABELS}")
                items.append((base / row["path"], row["label"], row.get("doc_type") or "unknown"))
    for folder, label in ((readable, "readable"), (unreadable, "unreadable")):
        if folder:
            items += [(p, label, "unknown") for p in sorted(folder.rglob("*"))
                      if p.suffix.lower() in IMAGE_EXT]
    return items


def evaluate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and rates from ``[{label, doc_type, passed, reason_codes, metrics}]``."""
    def rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
        readable = [r for r in rows if r["label"] == "readable"]
        unreadable = [r for r in rows if r["label"] == "unreadable"]
        fr = [r for r in readable if not r["passed"]]
        fa = [r for r in unreadable if r["passed"]]
        return {
            "readable": len(readable), "false_rejects": len(fr),
            "false_reject_rate": round(len(fr) / len(readable), 4) if readable else None,
            "unreadable": len(unreadable), "false_accepts": len(fa),
            "false_accept_rate": round(len(fa) / len(unreadable), 4) if unreadable else None,
            "false_reject_reasons": dict(Counter(c for r in fr for c in r["reason_codes"])),
        }

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_type[r["doc_type"]].append(r)
    heights = [r["metrics"].get("text_height_px") for r in results if r["metrics"].get("text_height_px")]
    return {"thresholds": thresholds(), "overall": rates(results),
            "by_doc_type": {k: rates(v) for k, v in sorted(by_type.items())},
            "median_text_height_px": statistics.median(heights) if heights else None}


def run(items: list[tuple[Path, str, str]]) -> dict[str, Any]:
    results = []
    for path, label, doc_type in items:
        arr = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        rep = quality.assess(arr) if arr is not None else quality.QualityReport(
            False, ["could not be decoded"], [], {}, ["undecodable"], [])
        results.append({"file": str(path), "label": label, "doc_type": doc_type, "passed": rep.passed,
                        "reason_codes": rep.reason_codes, "metrics": rep.metrics})
    report = evaluate(results)
    report["files"] = results
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="picture check: false-reject / false-accept report")
    ap.add_argument("--readable", type=Path)
    ap.add_argument("--unreadable", type=Path)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--json", type=Path, help="write the full report (with per-file results) here")
    args = ap.parse_args()
    items = load_items(args.readable, args.unreadable, args.manifest)
    if not items:
        raise SystemExit("no pictures: pass --readable / --unreadable folders or --manifest")
    report = run(items)
    short = {k: v for k, v in report.items() if k != "files"}
    print(json.dumps(short, indent=2, default=str))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
