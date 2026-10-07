"""Build the medicine-name list the result screen uses to keep a medicine out of the lab tests.

    python scripts/build_medicine_lexicon.py archive.zip  medicine_names.txt

Input: the "Extensive A-Z medicines dataset of India" (a CSV with ``name``, ``short_composition1/2`` and
``substitute0..4``). Output: one lower-case word per line, taken from the brand names, the generic names and the
substitute brands (letters only, 4+ letters), minus dosage-form words and minus every word that is also a test name.

The dataset is NOT stored in this repository (its licence is not ours to grant); only this script is. The output
goes where CDI_MEDICINE_LEXICON_PATH points (default /workspace/data/medicine_names.txt on the pod).
"""
from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from pathlib import Path

import importlib.util  # noqa: E402

# only the small pure module is needed: load it by path, so this script runs without the app's dependencies
_spec = importlib.util.spec_from_file_location(
    "test_names", Path(__file__).resolve().parents[1] / "src" / "cdi_adapter" / "extract" / "test_names.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
is_known_test = _mod.is_known_test

FORMS = set("""tablet tablets capsule capsules syrup suspension injection injections drops drop cream gel ointment lotion
solution powder sachet strip bottle vial ampoule infusion inhaler spray oral topical liquid shampoo soap oil paste
granules kit pack pen cartridge refill respules rotacaps nasal ophthalmic otic ear eye mouth wash tab cap inj syp susp
lozenge lozenges patch foam emulsion elixir tonic dusting medicated forte plus duo max extra advance advanced
only each with without and for the not per ltd limited pvt private pharma pharmaceuticals pharmaceutical
healthcare laboratories labs lifesciences remedies drugs biotech india indian
""".split())
# everyday, body and lab words that also occur in product names: never evidence that an entry is a medicine
COMMON = set("""serum fever chest liver live lives lipid lipids urine stool cardiac cardio cardiology kidney renal blood sugar
glucose count complete function profile culture scan ultra sound thyroid heart brain bone skin hair face body care
health active daily night day natural herbal protein relief cough cold pain mother baby child adult male female
fast first total normal water salt acid base free fresh clean soft hard long short super plus lite light ultra
intravenous fluid fluids therapy catheter surgery surgical hernia ventral abdominal ultrasound doppler monitoring
holter cardiac electro cardio gram graphy copy scopy""".split())
WORD = re.compile(r"[A-Za-z]{4,}")


def main(zip_path: str, out_path: str) -> int:
    words: set[str] = set()
    with zipfile.ZipFile(zip_path) as z:
        with z.open(z.namelist()[0]) as f:
            rows = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""))
            cols = ("name", "short_composition1", "short_composition2", "substitute0", "substitute1", "substitute2",
                    "substitute3", "substitute4")
            for row in rows:
                for c in cols:
                    for w in WORD.findall(row.get(c) or ""):
                        w = w.lower()
                        if w not in FORMS and w not in COMMON:
                            words.add(w)
    keep = sorted(w for w in words if not is_known_test(w))
    Path(out_path).write_text("\n".join(keep) + "\n", encoding="utf-8")
    print(f"{len(words):,} words found, {len(keep):,} kept ({len(words) - len(keep):,} dropped because they are test names)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
