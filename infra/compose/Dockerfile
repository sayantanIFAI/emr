FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# opencv (libGL/glib), PyMuPDF wheels need nothing extra, psql client for db/apply.sh
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY db ./db
COPY alembic.ini ./alembic.ini
COPY schemas ./schemas

RUN pip install --upgrade pip && pip install -e ".[dev]"

EXPOSE 8080
CMD ["uvicorn", "cdi_adapter.api:app", "--host", "0.0.0.0", "--port", "8080"]
