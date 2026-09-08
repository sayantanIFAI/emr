"""Drive S1->S2->S3 synchronously for every doc currently in the DB that hasn't
reached OCR yet, and print a compact report. For pod smoke-testing.

    python scripts/pipeline_smoke.py            # process pending docs
    python scripts/pipeline_smoke.py --all      # re-run classify+ocr for every doc
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from cdi_adapter.classify.service import classify_document
from cdi_adapter.db import session_scope
from cdi_adapter.ocr.service import ocr_document


def _targets(all_: bool) -> list[str]:
    q = "SELECT id::text FROM source_document" + (
        "" if all_ else " WHERE status IN ('pages_rendered','classified','error')"
    ) + " ORDER BY ingested_at"
    with session_scope() as sess:
        return [r[0] for r in sess.execute(text(q)).all()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    ids = _targets(args.all)
    print(f"{len(ids)} document(s) to process\n")
    for did in ids:
        try:
            c = classify_document(did)
            o = ocr_document(did)
            print(
                f"  {did[:8]}  {c.doc_type:<16} hw={str(c.is_handwritten):<5} "
                f"conf={c.confidence:.2f}  ocr={o.engine} blocks={o.n_blocks}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  {did[:8]}  ERROR  {exc}")

    print("\n--- pipeline_run summary ---")
    with session_scope() as sess:
        rows = sess.execute(
            text(
                "SELECT stage, status, count(*) FROM pipeline_run GROUP BY 1,2 ORDER BY 1,2"
            )
        ).all()
        for stage, status, n in rows:
            print(f"  {stage:<10} {status:<8} {n}")
        cls = sess.execute(
            text("SELECT doc_type, count(*) FROM doc_classification GROUP BY 1 ORDER BY 2 DESC")
        ).all()
        print("--- doc_classification ---")
        for dt, n in cls:
            print(f"  {dt:<18} {n}")
        blk = sess.execute(text("SELECT count(*) FROM ocr_block")).scalar_one()
        print(f"--- ocr_block rows: {blk} ---")


if __name__ == "__main__":
    main()
