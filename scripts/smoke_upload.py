"""Smoke test for a running deployment: sign in, upload one synthetic prescription image, wait for the
job, print the result summary. Synthetic text (printed), so it proves the plumbing and the printed path,
NOT handwriting accuracy.

    python scripts/smoke_upload.py http://127.0.0.1:8888 admin "$(cat /workspace/secrets/admin_password)"
"""
from __future__ import annotations

import io
import json
import sys
import time

import httpx
from PIL import Image, ImageDraw, ImageFont

LINES = [
    "CITY CARE CLINIC   OPD PRESCRIPTION",
    "Patient: Ravi Kumar   Age/Sex: 45 / M   Date: 05/10/2026",
    "Dr. A. Sen, MD (Medicine)  Reg No 12345",
    "Dx: Type 2 diabetes mellitus",
    "Rx  1. Tab Metformin 500 mg  1-0-1  x 30 days",
    "    2. Tab Paracetamol 650 mg  SOS",
    "Investigations: HbA1c, Fasting blood sugar (fasting 8-10 hrs), Serum creatinine",
    "Review after 2 weeks with reports",
]


def make_image() -> bytes:
    img = Image.new("RGB", (1240, 1000), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    y = 60
    for line in LINES:
        d.text((60, y), line, fill="black", font=font)
        y += 70
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def main() -> int:
    base, user, pw = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
    c = httpx.Client(base_url=base, auth=(user, pw), timeout=60)
    print("healthz:", c.get("/healthz").json())
    r = c.post("/api/jobs", files=[("files", ("smoke_rx.png", make_image(), "image/png"))],
               data={"grouping": "separate"})
    print("submit:", r.status_code, r.text[:200])
    r.raise_for_status()
    job = r.json()["job_id"]
    t0 = time.time()
    while time.time() - t0 < 600:
        j = c.get(f"/api/jobs/{job}").json()
        print(f"  {int(time.time() - t0):>3}s state={j['state']} " + " ".join(
            f"{d['filename']}:{d['status']}" for d in j["documents"]), flush=True)
        if j["state"] in ("done", "review", "error", "mismatch"):
            break
        time.sleep(5)
    res = c.get(f"/api/jobs/{job}/result.json").json()["results"][0]
    keep = {k: res.get(k) for k in ("status", "needs_check_count", "doc_type", "is_handwritten", "page_count")}
    print("result:", json.dumps(keep))
    print("lab_tests:", [t.get("as_written") for t in res.get("lab_tests", [])])
    print("medications:", [m.get("drug") for m in res.get("medications", [])])
    print("follow_up:", res.get("follow_up"))
    return 0 if j["state"] in ("done", "review") else 1


if __name__ == "__main__":
    raise SystemExit(main())
