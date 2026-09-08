#!/usr/bin/env bash
# Dump the adapter DB to /workspace so it survives a pod restart.
# The document bytes already persist (MinIO on /workspace); this captures the
# derived rows (clinical_fact, fhir_resource, review state, ...).
#
# Wire to cron for periodic snapshots:
#   (crontab -l 2>/dev/null; echo "*/15 * * * * bash /workspace/cdi/infra/runpod/snapshot.sh") | crontab -
set -euo pipefail
WS=/workspace
mkdir -p "$WS/backup"
set -a; . "$WS/cdi/.env"; set +a
PLAIN_URL="${CDI_DATABASE_URL#*+psycopg://}"
TS=$(date +%Y%m%d-%H%M%S)
pg_dump -Fc -d "postgresql://$PLAIN_URL" -f "$WS/backup/cdi.$TS.dump"
cp -f "$WS/backup/cdi.$TS.dump" "$WS/backup/cdi.dump"
ls -t "$WS"/backup/cdi.*.dump | tail -n +11 | xargs -r rm -f   # keep last 10
echo "snapshot -> $WS/backup/cdi.dump  ($(du -h "$WS/backup/cdi.dump" | cut -f1))"
