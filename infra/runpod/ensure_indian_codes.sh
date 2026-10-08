#!/usr/bin/env bash
# The national code lists (C-DAC / NRCeS: Common Lab Codes for India, Common Drug Codes for India) the lab-test gate reads.
#
# They are NOT in git: the packages are C-DAC's (CC BY 4.0 with SNOMED CT / LOINC terms, redistribution "within India"), so
# neither the packages nor the indexes built from them are stored in the (public) repository. They live where CDI_INDIAN_CODES_DIR
# points (/workspace/data). Without them the gate falls back to the small mapping table alone (about 200 aliases instead of
# about 1,100 lab names), and the Indian drug names are not known. This script runs on every start and makes sure they are there:
#
#   1. already built (clci_labs.json + cdci_drugs.json in the data dir)  -> nothing to do
#   2. the two C-DAC zips are in /workspace/data-src                       -> build the indexes from them
#   3. CDI_INDIAN_CODES_URL is set (a private URL of a tar.gz holding the two JSON files)   -> download and unpack
#   4. otherwise a loud warning (the pod still starts: it must never be left down for want of a reference list)
#
# Give the pod the data ONCE, from the machine that holds the packages:
#   bash infra/runpod/push_indian_codes.sh <ip> <ssh-port> [ssh-key]
set -uo pipefail
WS="${WS:-/workspace}"
REPO="${REPO:-$WS/cdi}"
DIR="${CDI_INDIAN_CODES_DIR:-$WS/data}"
SRC="${CDI_INDIAN_CODES_SRC:-$WS/data-src}"
mkdir -p "$DIR" "$SRC"

have() { [ -s "$DIR/clci_labs.json" ] && [ -s "$DIR/cdci_drugs.json" ]; }

if have; then
  echo "national code lists: present in $DIR"
else
  LABS="$(ls "$SRC"/common-lab-codes-for-india-*.zip 2>/dev/null | tail -1)"
  DRUGS="$(ls "$SRC"/CommonDrugCodesForIndia_FlatFilePackage*.zip 2>/dev/null | tail -1)"
  if [ -n "$LABS" ] && [ -n "$DRUGS" ]; then
    echo "national code lists: building from $SRC"
    ( cd "$REPO" && . .venv/bin/activate 2>/dev/null; python scripts/build_indian_codes.py --labs "$LABS" --drugs "$DRUGS" --out "$DIR" ) \
      || echo "(build failed)"
  elif [ -n "${CDI_INDIAN_CODES_URL:-}" ]; then
    echo "national code lists: downloading from the configured URL"
    tmp="$(mktemp)"
    if curl -fsSL "$CDI_INDIAN_CODES_URL" -o "$tmp" && tar -xzf "$tmp" -C "$DIR"; then echo "unpacked into $DIR"; else echo "(download failed)"; fi
    rm -f "$tmp"
  fi
fi
chmod 600 "$DIR"/clci_labs.json "$DIR"/cdci_drugs.json 2>/dev/null || true

if have; then
  python3 - "$DIR" <<'PY'
import json, sys
d = sys.argv[1]
print("national code lists: %d lab tests, %d drug brands loaded" % (
    len(json.load(open(d + "/clci_labs.json", encoding="utf-8"))["tests"]),
    len(json.load(open(d + "/cdci_drugs.json", encoding="utf-8")).get("brands", {}) or {})))
PY
else
  echo "######################################################################"
  echo "# WARNING: the national lab / drug code lists are NOT loaded."
  echo "# The lab-test gate runs on the small mapping table only and Indian drug names"
  echo "# are not known. Put the C-DAC zips in $SRC, or run from the machine that has them:"
  echo "#   bash infra/runpod/push_indian_codes.sh <ip> <ssh-port> [ssh-key]"
  echo "######################################################################"
fi
exit 0
