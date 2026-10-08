#!/usr/bin/env bash
# Run on the machine that HAS the C-DAC packages (not on the pod): builds the indexes and puts the packages and the indexes on the pod.
#
#   bash infra/runpod/push_indian_codes.sh <ip> <ssh-port> [ssh-key] [dir-with-the-zips]
#
# The dir defaults to ~/Downloads. After this the pod's start-up (ensure_indian_codes.sh) finds the data, and rebuilds it from the
# zips if the indexes are ever lost. SSH rules for these pods: always the direct port, never -t, scp with -P.
set -euo pipefail
IP="${1:?pod ip}"; PORT="${2:?ssh port}"; KEY="${3:-$HOME/.ssh/id_ed25519}"; ZIPS="${4:-$HOME/Downloads}"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
LABS="$(ls "$ZIPS"/common-lab-codes-for-india-*.zip | tail -1)"
DRUGS="$(ls "$ZIPS"/CommonDrugCodesForIndia_FlatFilePackage*.zip | tail -1)"
OUT="$(mktemp -d)"
python "$HERE/scripts/build_indian_codes.py" --labs "$LABS" --drugs "$DRUGS" --out "$OUT"
SSH=(ssh -p "$PORT" -i "$KEY" -o StrictHostKeyChecking=no "root@$IP")
"${SSH[@]}" 'mkdir -p /workspace/data /workspace/data-src'
scp -P "$PORT" -i "$KEY" -o StrictHostKeyChecking=no "$OUT/clci_labs.json" "$OUT/cdci_drugs.json" "root@$IP:/workspace/data/"
scp -P "$PORT" -i "$KEY" -o StrictHostKeyChecking=no "$LABS" "$DRUGS" "root@$IP:/workspace/data-src/"
"${SSH[@]}" 'chmod 600 /workspace/data/*.json; bash /workspace/cdi/infra/runpod/ensure_indian_codes.sh'
rm -rf "$OUT"
echo "done: restart the app on the pod to load them:  bash /workspace/cdi/infra/runpod/start_all.sh"
