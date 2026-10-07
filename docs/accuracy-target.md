# The 99 % target: what it means and what is measured

**Status today: NOT MEASURED on real handwriting.** The release gate (`eval/thresholds.json`) carries the owner's 99 % target
and therefore **blocks** until a labelled set of real prescriptions passes it. Nothing in this repository says the target is met.

## What is measured (`python -m cdi_adapter.eval.scorer --key <answer key> --results <result folder>`)

| Measure | Meaning | Gate |
|---|---|---|
| Accepted precision (lower bound) | Of the values the system accepted **on its own**, the share that are right (95 % Wilson lower bound) | >= 0.99 |
| Accepted CER | Character error rate of the accepted values: character edits / reference characters (insertions of a value that is not written count) | <= 0.01 |
| Accepted WER | The same, in words | <= 0.01 |
| Context error rate (upper bound) | A lab test linked to the wrong diagnosis or complaint on its line | <= 0.01 |
| Misfiled rate | A test filed as advice or a medicine, or a medicine or advice line filed as a test | <= 0.01 |
| Raw CER / WER | The same two rates over **everything** read, accepted or not. Reported, not gated | - |
| Coverage | The share of expected values accepted on their own | >= 0.60 |
| Sample size | At least 300 documents, 5000 accepted characters; a slice is judged only with 400 accepted values | - |

"Accepted" means the system stands behind the value without a person. Every other value (all patient names, every value read
from handwriting that a second reading does not support) goes to a person to confirm. So the 99 % is a promise about what is
**shown as final**, not about the raw reading of handwriting.

## What is not promised

* A raw 99 % CER / WER on free doctor handwriting. A 7B vision model reading phone photos of handwriting does not reach that;
  this is the engineering view (ESTIMATE), not a measurement. The raw rate is reported so it can be watched as it improves.
* "Grammar-free" handwriting. Text a doctor wrote ("T2DM", "to cont other meds as per cardiologist's advice") is copied as
  written. Correcting spelling or grammar would be a guess about what the doctor meant, so it is not done. The messages the
  application itself writes are fixed templates and are proofread.

## What moves the numbers (in the order that pays)

1. **Measure first.** A labelled set of real prescriptions (the owner's 100 000+ lines from about 150 doctors, plus whole-page
   answer keys). Without it no figure above can be stated.
2. **Auto-accept less, confirm more.** Raising the bar for "accepted" raises accepted precision at the cost of coverage.
3. **Constrained reading.** Lab tests snapped to the national list and the mapping table; medicines to the medicine list.
4. **Fine-tune the reader** on the labelled lines (LoRA on the line reader; story SW-S3).
5. **A typed patient name** at the front desk, and the registry, so a name is never a guess from handwriting.
6. A larger GPU / model, and more readings of the same line, for the lines that stay uncertain.
