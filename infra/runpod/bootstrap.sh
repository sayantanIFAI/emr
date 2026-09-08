#!/usr/bin/env bash
set -euo pipefail

WS="${WORKSPACE:-/workspace}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE="docker compose -f ${REPO_DIR}/infra/compose/docker-compose.yml"

echo ">> repo: ${REPO_DIR}"
echo ">> workspace: ${WS}"

mkdir -p "${WS}/data/inbox" "${WS}/data/processed" "${WS}/data/failed" \
         "${WS}/pgdata" "${WS}/models" "${WS}/hf-cache" "${WS}/data/gold" "${WS}/data/synthetic"

if ! docker compose version >/dev/null 2>&1; then
  echo ">> installing docker compose plugin"
  apt-get update -y && apt-get install -y docker-compose-plugin
fi

if [ ! -f "${REPO_DIR}/.env" ]; then
  cat > "${REPO_DIR}/.env" <<EOF
CDI_ENV=runpod
CDI_LOG_LEVEL=INFO
CDI_DATABASE_URL=postgresql+psycopg://cdi:cdi@postgres:5432/cdi
CDI_REDIS_URL=redis://redis:6379/0
CDI_S3_ENDPOINT_URL=http://minio:9000
CDI_S3_ACCESS_KEY=cdiadmin
CDI_S3_SECRET_KEY=cdiadminsecret
CDI_S3_BUCKET=cdi-documents
CDI_INBOX_DIR=/data/inbox
CDI_PROCESSED_DIR=/data/processed
CDI_FAILED_DIR=/data/failed
CDI_LLM_BASE_URL=http://host.docker.internal:8000/v1
EOF
  echo ">> wrote ${REPO_DIR}/.env"
fi

echo ">> starting stack"
${COMPOSE} up -d --build

echo ">> waiting for API health"
for i in $(seq 1 30); do
  if curl -sf localhost:8080/healthz >/dev/null; then echo "   API healthy"; break; fi
  sleep 2
done

echo
echo "Done. Next:"
echo "  python ${REPO_DIR}/scripts/make_sample_docs.py --out ${WS}/data/inbox --count 3"
echo "  curl -s localhost:8080/healthz | jq"
