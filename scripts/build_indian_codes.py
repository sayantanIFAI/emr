"""Build the runtime indexes from the Indian national code sets (NRCeS / C-DAC).

    python scripts/build_indian_codes.py --labs common-lab-codes-for-india-20260629.zip \
        --drugs CommonDrugCodesForIndia_FlatFilePackage.zip --out /workspace/data

Writes (all plain JSON, small, loaded at start-up):

* ``clci_labs.json``   Common Lab Codes for India (a curated LOINC subset): for each test a General Name, its aliases (the
                       bracketed forms such as "TSH; Thyrotropin"), the specimen, the LOINC code, FSN and long name.
* ``cdci_drugs.json``  Common Drug Codes for India: Indian brand names (ProductMaster) -> product id -> generic ids
                       (BrandMaster) -> generic names (GenericMaster), plus substance names (SubstanceMaster).

Licence: the packages are C-DAC's (CC BY 4.0 with SNOMED CT / LOINC terms, redistribution "within India"), so neither
the packages nor these indexes are stored in the repository; they live where ``CDI_INDIAN_CODES_DIR`` points.
The Drug Information Service Bundle (a Java service over the same drug data) is not needed: its content is these files.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path


def _tsv(z: zipfile.ZipFile, name: str) -> list[list[str]]:
    member = next(n for n in z.namelist() if n.endswith("/" + name) or n == name)
    text = z.read(member).decode("utf-8", "replace")
    rows = [ln.split("\t") for ln in text.splitlines() if ln.strip()]
    return rows[1:]                                                  # drop the header


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").casefold())).strip()


# ----------------------------------------------------------------------------------------------------- labs
def build_labs(zip_path: str) -> dict:
    z = zipfile.ZipFile(zip_path)
    member = next(n for n in z.namelist() if n.endswith(".csv"))
    rows = list(csv.DictReader(io.TextIOWrapper(z.open(member), encoding="utf-8-sig", newline="")))
    tests: list[dict] = []
    for r in rows:
        general = (r.get("General Name") or "").strip()
        loinc = (r.get("LOINC Code") or "").strip()
        if not general or not loinc:
            continue
        # "Thyroid Stimulating Hormone (TSH; Thyrotropin), Blood" -> name, aliases, specimen
        specimen = general.rsplit(",", 1)[1].strip() if "," in general else None
        base = general.rsplit(",", 1)[0].strip() if "," in general else general
        aliases = []
        for inner in re.findall(r"\(([^)]*)\)", base):
            aliases += [a.strip() for a in inner.split(";") if a.strip()]
        name = re.sub(r"\s*\([^)]*\)", "", base).strip()
        tests.append({"name": name, "aliases": aliases, "specimen": specimen, "loinc": loinc,
                      "fsn": (r.get("Fully-Specified Name (FSN)") or "").strip(),
                      "lcn": (r.get("Long Common Name") or "").strip()})
    return {"source": "Common Lab Codes for India (LOINC 2.82 subset), NRCeS C-DAC", "tests": tests}


# ----------------------------------------------------------------------------------------------------- drugs
def _strip_strength(name: str) -> str:
    """'Carbimazole 5 mg oral tablet' -> 'Carbimazole'; 'Flucret 30 mg + 4 mg oral tablet' -> 'Flucret'."""
    m = re.search(r"\s\d", name)
    return (name[:m.start()] if m else name).strip()


def build_drugs(zip_path: str) -> dict:
    z = zipfile.ZipFile(zip_path)
    substances = {r[0]: r[1].strip() for r in _tsv(z, "SubstanceMaster.txt") if len(r) > 1 and r[1].strip()}
    generics = {r[0]: r[1].strip() for r in _tsv(z, "GenericMaster.txt") if len(r) > 1 and r[1].strip()}
    products = {r[0]: r[1].strip() for r in _tsv(z, "ProductMaster.txt") if len(r) > 1 and r[1].strip()}
    product_generics: dict[str, set[str]] = defaultdict(set)
    for r in _tsv(z, "BrandMaster.txt"):
        # Identifier, Brand Name, Product Identifier, Supplier Identifier, Generic Identifier, ...
        if len(r) > 4 and r[2].strip() and r[4].strip():
            product_generics[r[2].strip()].add(r[4].strip())
    brands: dict[str, list] = {}
    for pid, pname in products.items():
        key = norm(_strip_strength(pname))
        if len(key) < 3:
            continue
        gids = sorted(product_generics.get(pid, ()))
        brands.setdefault(key, []).append([pid, pname, gids])
    generic_stems: dict[str, str] = {}
    for gid, gname in generics.items():
        generic_stems.setdefault(norm(_strip_strength(gname)), gid)
    subst = {norm(n): sid for sid, n in substances.items() if len(norm(n)) >= 3}
    used = {g for lst in brands.values() for _p, _n, gids in lst for g in gids}
    generic_names = {gid: generics[gid] for gid in used if gid in generics}
    return {"source": "Common Drug Codes for India (SNOMED CT National Extension), NRCeS C-DAC",
            "brands": brands, "generic_stems": generic_stems, "substances": subst, "generic_names": generic_names}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labs"), ap.add_argument("--drugs"), ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.labs:
        d = build_labs(a.labs)
        (out / "clci_labs.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        print(f"labs : {len(d['tests']):,} tests, {sum(len(t['aliases']) for t in d['tests']):,} aliases")
    if a.drugs:
        d = build_drugs(a.drugs)
        (out / "cdci_drugs.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        print(f"drugs: {len(d['brands']):,} brand names, {len(d['generic_stems']):,} generics, {len(d['substances']):,} substances")
    return 0


if __name__ == "__main__":
    sys.exit(main())
