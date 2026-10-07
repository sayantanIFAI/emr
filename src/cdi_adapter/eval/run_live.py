"""Send an answer key's images to a RUNNING service and save each result.v1 JSON (ENT-S3, LS-S10).

    python -m cdi_adapter.eval.run_live --base http://127.0.0.1:8888 --user admin --password-file /workspace/secrets/admin_password \
        --key eval_set/key.jsonl --out eval_set/results --limit 40

It records, per document, the seconds from the Send to a finished result (MEASURED on whatever hardware the service
runs on) so the scoreboard and a timing table come from the same run.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx


def run(base: str, auth: tuple[str, str], key: list[dict], img_dir: Path, out: Path, *, concurrency: int = 3,
        timeout_s: float = 900.0) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    c = httpx.Client(base_url=base, auth=auth, timeout=120)
    times: dict[str, float] = {}
    pending: dict[str, tuple[str, float]] = {}                                    # doc key id -> (job id, t0)
    todo = list(key)
    deadline = time.time() + timeout_s * max(1, len(key))
    while (todo or pending) and time.time() < deadline:
        while todo and len(pending) < concurrency:
            k = todo.pop(0)
            r = c.post("/api/jobs", files=[("files", (k["image"], (img_dir / k["image"]).read_bytes(), "image/png"))],
                       data={"grouping": "separate"}, headers={"Idempotency-Key": f"live-{k['id']}"})
            r.raise_for_status()
            pending[k["id"]] = (r.json()["job_id"], time.time())
        for kid, (jid, t0) in list(pending.items()):
            j = c.get(f"/api/jobs/{jid}").json()
            if j["state"] in ("done", "review", "error", "mismatch"):
                res = c.get(f"/api/jobs/{jid}/result.json").json()["results"][0]
                (out / f"{kid}.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
                times[kid] = round(time.time() - t0, 1)
                del pending[kid]
        time.sleep(2)
    secs = list(times.values())
    return {"documents": len(times), "concurrency": concurrency, "seconds_per_document_mean": round(statistics.fmean(secs), 1) if secs else None,
            "seconds_p50": round(statistics.median(secs), 1) if secs else None,
            "seconds_max": max(secs) if secs else None, "unfinished": sorted(pending)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password-file", required=True, type=Path)
    ap.add_argument("--key", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=3)
    a = ap.parse_args(argv)
    key = [json.loads(ln) for ln in a.key.read_text("utf-8").splitlines() if ln.strip()]
    if a.limit:
        key = key[: a.limit]
    summary = run(a.base, (a.user, a.password_file.read_text().strip()), key, a.key.parent, a.out, concurrency=a.concurrency)
    print(json.dumps(summary, indent=2))
    (a.out / "_timing.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
