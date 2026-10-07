#!/usr/bin/env bash
# Pack everything that cannot be re-downloaded or re-built into ONE file, so it can be copied off the pod.
#
#   bash /workspace/cdi/infra/runpod/backup_pack.sh [/path/to/cdi-state.tar.gz]
#
# In the pack: the generated secrets (admin password, database and object-store keys), a fresh database
# dump, the object store (the stored prescription images and their results), the object-store identities,
# the Redis data and the inbox folders. NOT in it: the code (git) and the models (downloaded again).
#
# /workspace is only as safe as the volume under it: when it is the pod's own container disk (see
# `mountpoint /workspace`) a stopped or recreated pod loses all of it. Copy the pack somewhere else:
#   scp -P <port> root@<ip>:/workspace/offpod/cdi-state.tar.gz .
set -uo pipefail
WS="${WS:-/workspace}"
OUT="${1:-$WS/offpod/cdi-state.tar.gz}"
mkdir -p "$(dirname "$OUT")"

# a fresh dump first (the object store is copied after it, so every row it points to is in the pack)
bash "$WS/cdi/infra/runpod/snapshot.sh" >/dev/null 2>&1 || echo "warning: the database snapshot failed; the pack holds the last one" >&2

items=()
for p in secrets backup/cdi.dump seaweedfs seaweedfs-data redis data; do
  [ -e "$WS/$p" ] && items+=("$p")
done
[ "${#items[@]}" -gt 0 ] || { echo "nothing to pack under $WS" >&2; exit 1; }

tmp="$OUT.part"
tar -czf "$tmp" -C "$WS" "${items[@]}" 2>/dev/null
rc=$?
# 1 = "a file changed while it was read" (the object store is live): the pack is still good
[ "$rc" -le 1 ] || { echo "tar failed ($rc)" >&2; rm -f "$tmp"; exit 1; }
mv -f "$tmp" "$OUT"
( cd "$(dirname "$OUT")" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
chmod 600 "$OUT" 2>/dev/null || true       # it holds the passwords
echo "pack -> $OUT  ($(du -h "$OUT" | cut -f1), $(date -u +%FT%TZ))"
