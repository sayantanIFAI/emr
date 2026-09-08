"""Generate synthetic scanned-style documents for local testing.

Not clinical data - fictional patients, templated text, deliberately imperfect
(rotated, noisy) so the ingest deskew/denoise path gets exercised. Drops PDFs +
JSON sidecars into the inbox.
"""
from __future__ import annotations

import argparse
import io
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymupdf as fitz
import numpy as np
from PIL import Image

IST = timezone(timedelta(hours=5, minutes=30))

PRESCRIPTION = """CITY CARE HOSPITAL   (HFR: IN-XXXX)
OPD PRESCRIPTION

Patient: {name}      MRN: {mrn}
Age/Sex: {age} / {sex}      Date: {date}
Dept: General Medicine     Dr. A. Sen (Reg. No. 12345)

Diagnosis: Essential hypertension; Type 2 diabetes mellitus

Rx:
  1. Tab Amlodipine 5 mg      -- 1-0-0   x 30 days
  2. Tab Metformin 500 mg     -- 1-0-1   x 30 days (after food)
  3. Tab Atorvastatin 10 mg   -- 0-0-1   x 30 days

Advice: Low salt diet. Home BP monitoring. Review after 2 weeks.
BP today: 148/92 mmHg   Wt: 78 kg
"""

LAB_REPORT = """CITY CARE HOSPITAL - DEPARTMENT OF LABORATORY MEDICINE
BIOCHEMISTRY REPORT

Patient: {name}     MRN: {mrn}      Age/Sex: {age}/{sex}
Collected: {date}    Reported: {date}

TEST                    RESULT     UNIT        REF. RANGE      FLAG
--------------------------------------------------------------------
HbA1c                   7.8        %           4.0 - 5.6        H
Fasting Blood Sugar     142        mg/dL       70 - 100        H
Serum Creatinine        0.9        mg/dL       0.7 - 1.3       N
Total Cholesterol       214        mg/dL       < 200          H
LDL Cholesterol         132        mg/dL       < 100          H
--------------------------------------------------------------------
Verified by: Dr. R. Iyer, MD (Biochem)
"""

VITALS = """CITY CARE HOSPITAL - NURSING VITALS / INTAKE SHEET

Patient: {name}   MRN: {mrn}   Date/Time: {date} 09:15

BP ....... 138 / 86 mmHg
Pulse .... 78 /min
Temp ..... 98.4 F
SpO2 ..... 98 %
Weight ... 71 kg
Height ... 165 cm

Chief complaint: mild incision-site pain, otherwise well.
Allergy: Penicillin (rash) - reported by patient.
"""

TEMPLATES = {
    "prescription": PRESCRIPTION,
    "lab_report": LAB_REPORT,
    "vitals_sheet": VITALS,
}

NAMES = ["Anjali Das", "Rahul Verma", "Meera Nair", "Sofia Khan", "Arjun Rao"]


def _text_to_pdf_bytes(text: str, *, rotate: float, noise: float) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 pt
    page.insert_text((54, 70), text, fontsize=11, fontname="courier")
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    doc.close()

    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    if abs(rotate) > 0.01:
        img = img.rotate(rotate, expand=True, fillcolor=255, resample=Image.BICUBIC)
    if noise > 0:
        arr = np.asarray(img).astype(np.int16)
        arr = arr + np.random.normal(0, noise, arr.shape).astype(np.int16)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    out = fitz.open()
    rect = fitz.Rect(0, 0, img.width, img.height)
    pg = out.new_page(width=img.width, height=img.height)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    pg.insert_image(rect, stream=buf.getvalue())
    data = out.tobytes()
    out.close()
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./data/inbox")
    ap.add_argument("--count", type=int, default=3, help="patients")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    now = datetime.now(IST)
    made = 0
    for i in range(args.count):
        name = NAMES[i % len(NAMES)]
        mrn = f"LG-{80000 + i * 17}"
        age = random.randint(28, 72)
        sex = random.choice(["M", "F"])
        for j, (dtype, tmpl) in enumerate(TEMPLATES.items()):
            date = (now - timedelta(days=random.randint(0, 400))).strftime("%d-%b-%Y")
            body = tmpl.format(name=name, mrn=mrn, age=age, sex=sex, date=date)
            pdf = _text_to_pdf_bytes(
                body,
                rotate=random.uniform(-4, 4),
                noise=random.uniform(2, 8),
            )
            stem = f"{mrn}_{dtype}_{i}{j}"
            (out / f"{stem}.pdf").write_bytes(pdf)
            (out / f"{stem}.pdf.json").write_text(
                json.dumps(
                    {
                        "legacy_ref": f"DOC-{stem}",
                        "legacy_patient_ref": mrn,
                        "captured_at": (now - timedelta(days=1)).isoformat(),
                        "hint_doc_type": dtype,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            made += 1
    print(f"wrote {made} sample documents (+ sidecars) to {out}")


if __name__ == "__main__":
    main()
