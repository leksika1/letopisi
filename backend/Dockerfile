FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
COPY pipeline/ingest/requirements.txt pipeline/ingest/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt -r pipeline/ingest/requirements.txt
COPY backend/__init__.py backend/
COPY backend/app/ backend/app/
COPY contracts/schema.sql contracts/schema.sql
COPY contracts/observation.schema.json contracts/export_columns.json contracts/
COPY contracts/fixtures/ contracts/fixtures/
COPY pipeline/load.py pipeline/load.py
COPY pipeline/__init__.py pipeline/__init__.py
COPY pipeline/ingest/ pipeline/ingest/
COPY pipeline/normalize/ pipeline/normalize/
COPY pipeline/extract/ pipeline/extract/
COPY dictionaries/*.csv dictionaries/
RUN mkdir -p /app/data/raw && chown -R 65534:65534 /app/data

USER 65534:65534
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
