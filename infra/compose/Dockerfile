FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# opencv (libGL/glib), pypdfium2 wheels need nothing extra, psql client for db/apply.sh
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY db ./db
COPY alembic.ini ./alembic.ini
COPY schemas ./schemas

# runtime only: no pytest / ruff / mypy in the image (editable, so schemas/ and the licence files resolve)
RUN pip install --upgrade pip && pip install -e ".[ocr]"

# never run as root
RUN useradd --system --create-home --uid 10001 cdi && chown -R cdi:cdi /app
USER cdi

EXPOSE 8080
CMD ["python", "-m", "cdi_adapter.webapp"]
