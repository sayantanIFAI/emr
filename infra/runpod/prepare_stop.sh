#!/usr/bin/env bash
# Run this BEFORE stopping, restarting or deleting the pod: it writes a fresh backup pack and prints the
# command that copies it to your computer.
set -uo pipefail
WS="${WS:-/workspace}"
bash "$WS/cdi/infra/runpod/backup_pack.sh" || exit 1
IP="$(tr '\0' '\n' < /proc/1/environ 2>/dev/null | sed -n 's/^RUNPOD_PUBLIC_IP=//p')"
PORT="$(tr '\0' '\n' < /proc/1/environ 2>/dev/null | sed -n 's/^RUNPOD_TCP_PORT_22=//p')"
echo
echo "Copy it off the pod (from YOUR computer):"
echo "  scp -P ${PORT:-<port>} -i ~/.ssh/id_ed25519 root@${IP:-<ip>}:$WS/offpod/cdi-state.tar.gz* ."
if ! mountpoint -q "$WS"; then
  echo
  echo "WARNING: $WS is the pod's own container disk, not a volume: a stop or reset deletes it."
fi
