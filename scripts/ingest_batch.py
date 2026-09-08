"""Batch/backfill ingest: walk a directory tree and ingest every document.

Usage:
    python scripts/ingest_batch.py /path/to/legacy/export --channel batch
    python scripts/ingest_batch.py ./data/inbox --async   # enqueue on celery instead
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--channel", default="batch")
    ap.add_argument("--async", dest="async_", action="store_true", help="enqueue via celery")
    args = ap.parse_args()

    root = Path(args.root)
    files = [p for p in root.rglob("*") if p.suffix.lower() in EXTS]
    print(f"found {len(files)} documents under {root}")

    if args.async_:
        from cdi_adapter.worker import ingest_file

        for p in files:
            meta = _sidecar(p) | {"source_channel": args.channel}
            ingest_file.delay(str(p), meta)
        print(f"enqueued {len(files)} tasks")
        return

    from cdi_adapter.ingest.service import ingest_bytes

    ok = dedup = err = 0
    for p in files:
        meta = _sidecar(p)
        try:
            res = ingest_bytes(
                p.read_bytes(),
                filename=p.name,
                source_channel=args.channel,
                legacy_ref=meta.get("legacy_ref"),
                legacy_patient_ref=meta.get("legacy_patient_ref"),
            )
            dedup += res.deduplicated
            ok += not res.deduplicated
            print(f"  {p.name}: {res.document_id} pages={res.page_count} dedup={res.deduplicated}")
        except Exception as exc:  # noqa: BLE001
            err += 1
            print(f"  {p.name}: ERROR {exc}")
    print(f"done. new={ok} dedup={dedup} errors={err}")


def _sidecar(p: Path) -> dict:
    for cand in (p.with_suffix(p.suffix + ".json"), p.with_suffix(".json")):
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
    return {}


if __name__ == "__main__":
    main()
