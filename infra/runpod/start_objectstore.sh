#!/usr/bin/env bash
# Start the object store: SeaweedFS (Apache-2.0) serving S3 on 127.0.0.1:9000. Idempotent.
# Replaces MinIO (AGPL, archived upstream, source-only): docs/object-store.md.
#
#   - one process (`weed server -s3`: master + volume + filer + S3 gateway); data on /workspace
#   - EVERY listener is bound to 127.0.0.1; only the S3 port is meant to be reached, behind TLS
#   - outbound telemetry OFF; Iceberg / Lance gateways OFF; embedded IAM API OFF; bucket is NOT
#     auto-created; CORS limited to $CDI_S3_ALLOWED_ORIGINS
#   - two identities: the application key (CDI_S3_ACCESS_KEY / _SECRET_KEY) may only read, write
#     and list the one bucket; a separate admin key exists only to create that bucket
#   - the binary is pinned by version and by the sha256 GitHub publishes for the release asset
#     (checked here against a download; upstream's own .md5 file is not trusted)
#
# Flags below were checked against `weed server -h` of release 4.48 and exercised on a local PC
# (docs/object-store.md "Verified"). NOT yet run on the pod's MooseFS /workspace.
set -euo pipefail

WS="${WS:-/workspace}"
REPO="${REPO:-$WS/cdi}"
BIN="${WEED_BIN:-$WS/bin/weed}"
DATA="${CDI_OBJECTSTORE_DIR:-$WS/seaweedfs-data}"
CONF="${CDI_OBJECTSTORE_CONF:-$WS/seaweedfs}"
LOG="${CDI_OBJECTSTORE_LOG:-$WS/seaweedfs.log}"
PY="${PYTHON:-python3}"
PORT=9000

WEED_VERSION=4.48
WEED_ASSET=linux_amd64.tar.gz
WEED_SHA256=4a7d108384d044d95212d1342cdda9533fa55842c1c9b41f606ca3c8a9561124
WEED_URL="https://github.com/seaweedfs/seaweedfs/releases/download/${WEED_VERSION}/${WEED_ASSET}"

[ -f "$REPO/.env" ] && { set -a; . "$REPO/.env"; set +a; }
BUCKET="${CDI_S3_BUCKET:-cdi-documents}"
APP_KEY="${CDI_S3_ACCESS_KEY:?CDI_S3_ACCESS_KEY is not set}"
APP_SECRET="${CDI_S3_SECRET_KEY:?CDI_S3_SECRET_KEY is not set}"
ORIGINS="${CDI_S3_ALLOWED_ORIGINS:-http://127.0.0.1:8888}"

mkdir -p "$(dirname "$BIN")" "$DATA" "$CONF"

# ---- 1. the pinned binary -------------------------------------------------------------------
if [ ! -x "$BIN" ]; then
  tmp="$(mktemp -d)"
  curl -fsSL "$WEED_URL" -o "$tmp/$WEED_ASSET"
  echo "$WEED_SHA256  $tmp/$WEED_ASSET" | sha256sum -c - >/dev/null \
    || { echo "weed $WEED_VERSION: sha256 mismatch, refusing to install" >&2; rm -rf "$tmp"; exit 1; }
  tar -xzf "$tmp/$WEED_ASSET" -C "$tmp"
  install -m 0755 "$tmp/weed" "$BIN"
  rm -rf "$tmp"
fi

# ---- 2. identities (rewritten every start from the environment; secrets never on a command line) --
ADMIN_ENV="$CONF/admin.env"
if [ -z "${CDI_S3_ADMIN_ACCESS_KEY:-}" ] || [ -z "${CDI_S3_ADMIN_SECRET_KEY:-}" ]; then
  if [ ! -s "$ADMIN_ENV" ]; then
    umask 077
    printf 'CDI_S3_ADMIN_ACCESS_KEY=%s\nCDI_S3_ADMIN_SECRET_KEY=%s\n' \
      "admin$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
      "$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')" > "$ADMIN_ENV"
  fi
  set -a; . "$ADMIN_ENV"; set +a
fi
APP_KEY="$APP_KEY" APP_SECRET="$APP_SECRET" BUCKET="$BUCKET" \
ADMIN_KEY="$CDI_S3_ADMIN_ACCESS_KEY" ADMIN_SECRET="$CDI_S3_ADMIN_SECRET_KEY" \
"$PY" - "$CONF/s3.json" <<'PYEOF'
import json, os, sys
b = os.environ["BUCKET"]
cfg = {"identities": [
    {"name": "cdi-bucket-admin",
     "credentials": [{"accessKey": os.environ["ADMIN_KEY"], "secretKey": os.environ["ADMIN_SECRET"]}],
     "actions": ["Admin"]},
    {"name": "cdi-app",
     "credentials": [{"accessKey": os.environ["APP_KEY"], "secretKey": os.environ["APP_SECRET"]}],
     "actions": [f"Read:{b}", f"Write:{b}", f"List:{b}"]},
]}
with open(sys.argv[1], "w") as f:
    json.dump(cfg, f)
PYEOF
chmod 600 "$CONF/s3.json" 2>/dev/null || true

# ---- 3. start ---------------------------------------------------------------------------------
start_server() {
  # Background the command itself (see start_mlserve.sh): a backgrounded `cd && ...` list keeps
  # this script's stdout open for the server's lifetime and hangs a caller reading our output.
  $(command -v setsid || true) nohup "$BIN" server \
    -dir="$DATA" -ip=127.0.0.1 -ip.bind=127.0.0.1 -filer.port=18888 \
    -master.telemetry=false -master.volumeSizeLimitMB=1024 -volume.max=0 \
    -s3 -s3.config="$CONF/s3.json" -s3.port="$PORT" -s3.ip.bind=127.0.0.1 \
    -s3.port.iceberg=0 -s3.port.lance=0 -s3.iam=false -s3.autoCreateBucket=false \
    -s3.allowedOrigins="$ORIGINS" \
    > "$LOG" 2>&1 < /dev/null &
}
healthy() { curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; }
alive() { pgrep -f "[b]in/weed server" >/dev/null 2>&1; }
healthy || start_server
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 \
  || { echo "object store did not answer on :${PORT} in 60s; tail log:" >&2; tail -30 "$LOG" >&2; exit 1; }

# ---- 4. the bucket (created with the admin identity; the application key cannot) ----------------
# The S3 gateway answers before the filer is ready for `weed shell`, so retry; "already exists" is
# success (a listing right after start can come back empty, so it is not used to decide).
created=""
for _ in $(seq 1 30); do
  if ! alive; then
    echo "object store process is gone - starting it again" >&2
    start_server
    for _w in $(seq 1 30); do healthy && break; sleep 1; done
  fi
  out="$(echo "s3.bucket.create -name ${BUCKET}" | timeout 25 "$BIN" shell -master=127.0.0.1:9333 2>&1 || true)"
  case "$out" in *"created bucket"*|*"already exists"*) created=1; break ;; esac
  sleep 1
done
[ -n "$created" ] || { echo "could not create bucket ${BUCKET}: ${out:-no output}" >&2; exit 1; }
echo "object store ready: http://127.0.0.1:${PORT}  bucket ${BUCKET}"
