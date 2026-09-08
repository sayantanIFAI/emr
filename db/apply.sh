#!/usr/bin/env bash
# Apply schema + views directly with psql (alternative to `alembic upgrade head`).
# Usage: DATABASE_URL=postgres://... ./db/apply.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${DATABASE_URL:?set DATABASE_URL (e.g. postgresql://cdi:cdi@localhost:5432/cdi)}"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f "$HERE/schema.sql"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f "$HERE/views.sql"
echo "schema + views applied"
