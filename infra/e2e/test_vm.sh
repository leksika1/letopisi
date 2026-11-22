#!/usr/bin/env bash
# Run from a machine with SSH, rsync and Python 3.12 with Playwright Chromium.
# The target must be a fresh, disposable Docker host. It retains the QA deployment.
# Usage: bash infra/e2e/test_vm.sh --disposable [user@]ssh-alias
set -euo pipefail

if [[ $# != 2 || $1 != --disposable || $2 == -* || ! $2 =~ ^([a-zA-Z0-9_.-]+@)?[a-zA-Z0-9_.-]+$ ]]; then
    echo "Usage: bash infra/e2e/test_vm.sh --disposable [user@]ssh-alias" >&2
    exit 2
fi
host=$2
python=${E2E_PYTHON:-python3}
cd "$(dirname "$0")/../.."
"$python" -c 'import httpx; from playwright.sync_api import sync_playwright'

ssh -o BatchMode=yes "$host" bash -se <<'PREFLIGHT'
set -euo pipefail
docker compose version
containers=$(docker ps -aq)
volumes=$(docker volume ls -q)
if [[ -e ~/letopisi || -L ~/letopisi || -n $containers || -n $volumes ]]; then
    echo "Use an unused QA VM: an existing deployment, container or volume was found." >&2
    exit 1
fi
if [[ -n ${DATABASE_URL:-} || -n ${COMPOSE_ENV_FILES:-} || -n ${COMPOSE_FILE:-} || -n ${DOCKER_HOST:-} ||
      -n ${YC_API_KEY:-} || -n ${S3_ACCESS_KEY:-} || -n ${S3_SECRET_KEY:-} ]]; then
    echo "Use a fresh QA shell and local Docker daemon, without database overrides or cloud credentials." >&2
    exit 1
fi
endpoint=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
[[ $endpoint == unix:///* ]] || { echo "A local Docker daemon is required." >&2; exit 1; }
PREFLIGHT

bash infra/deploy.sh "$host"
ssh -o BatchMode=yes "$host" 'cd ~/letopisi/current/infra && docker compose exec -T backend python -' <<'SEED'
import json
import subprocess
import sys

from sqlalchemy import text
from backend.app.db import database_engine

engine = database_engine()
assert engine.url.host == "postgres" and engine.url.database == "letopisi", "Unexpected database target"
with engine.connect() as conn:
    assert conn.scalar(text("SELECT count(*) FROM observations")) == 0
    assert conn.scalar(text("SELECT EXISTS (SELECT FROM pg_extension WHERE extname='pg_trgm')"))
    assert conn.scalar(text("SELECT cardinality(show_trgm('лось'))")) > 0
engine.dispose()
for attempt in range(2):
    counts = json.loads(subprocess.check_output([sys.executable, "pipeline/load.py"]))
    if attempt == 0:
        assert counts["observations"] == 38 and counts["documents"] == 3, counts
    else:
        assert counts and all(count == 0 for count in counts.values()), counts
SEED

port=$("$python" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -L "127.0.0.1:$port:127.0.0.1:8000" "$host" &
tunnel=$!
trap 'kill "$tunnel" 2>/dev/null || true; wait "$tunnel" 2>/dev/null || true' EXIT
"$python" - "$port" <<'CHECK_WEB'
import csv
import io
import os
import sys
import time
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import expect, sync_playwright

origin = f"http://127.0.0.1:{sys.argv[1]}"
with httpx.Client(base_url=origin, timeout=8, trust_env=False) as api:
    for retry in range(40):
        try:
            response = api.get("/api/health")
            break
        except httpx.ConnectError:
            if retry == 39:
                raise
            time.sleep(0.25)
    response.raise_for_status()
    assert response.json()["db"] is True
    filters = {"document_id": 1}
    search = api.get("/api/observations", params=filters)
    search.raise_for_status()
    matches = search.json()
    ids = {row["id"] for row in matches["items"]}
    assert len(ids) == matches["total"] == 5
    exported = api.get("/api/export", params={**filters, "format": "csv"})
    exported.raise_for_status()
    rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == len(ids) + 1
    assert {int(urlsplit(row[13]).path.split("/")[-1]) for row in rows[1:]} == ids

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=os.getenv("E2E_CHROMIUM") or None)
    page = browser.new_page()
    page.goto(origin)
    expect(page.locator("[data-observation-id]").first).to_be_visible()
    assert page.get_by_text("режим моков", exact=True).count() == 0
    assert page.evaluate("navigator.serviceWorker.getRegistrations().then(reg => reg.length)") == 0
    browser.close()
CHECK_WEB
echo "VM smoke passed: deploy, API, frontend, PostgreSQL, seed, search and CSV."
