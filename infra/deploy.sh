#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 1 || $1 == -* || ! $1 =~ ^([a-zA-Z0-9_.-]+@)?[a-zA-Z0-9_.-]+$ ]]; then
    echo "Usage: bash infra/deploy.sh [user@]ssh-host" >&2
    exit 2
fi
host=$1
cd "$(dirname "$0")/.."

# Each release gets a fresh build context. pgdata belongs to the stable Compose project.
release=$(ssh "$host" 'mkdir -p letopisi/releases && mktemp -d letopisi/releases/deploy.XXXXXXXX')
[[ $release =~ ^letopisi/releases/deploy\.[[:alnum:]]+$ ]]

files=(
    backend/Dockerfile backend/Dockerfile.dockerignore backend/requirements.txt
    backend/__init__.py backend/app/
    pipeline/__init__.py pipeline/load.py pipeline/ingest/
    pipeline/normalize/ pipeline/extract/__init__.py pipeline/extract/llm.py pipeline/extract/prompt.py
    dictionaries/*.csv
    contracts/schema.sql contracts/fixtures/ contracts/observation.schema.json contracts/export_columns.json
    infra/docker-compose.yml infra/frontend.Dockerfile infra/frontend.Dockerfile.dockerignore
    infra/nginx.conf
    frontend/package*.json frontend/tsconfig*.json frontend/*.config.* frontend/index.html
    frontend/public/ frontend/src/
)
if [[ -f frontend/dist/index.html ]]; then
    files+=(frontend/dist/)
fi

rsync --archive --relative -e ssh \
    --exclude='.env' --exclude='.env.*' --exclude='*.pem' --exclude='*.key' \
    --exclude='__pycache__/' --exclude='data/' \
    "${files[@]}" "$host:$release/"

ssh "$host" bash -s -- "$release" <<'REMOTE'
set -euo pipefail
env_args=()
if [[ -f letopisi/.env ]]; then
    env_args=(--env-file "$PWD/letopisi/.env")
fi
cd "$1/infra"
docker compose "${env_args[@]}" up -d --build --wait
ln -sfn "releases/${1##*/}" "$HOME/letopisi/current"
REMOTE
