#!/usr/bin/env bash
# Разворачивает стенд на чистой VM (Ubuntu 22.04/24.04) прямо из клона репозитория.
#
#   git clone https://github.com/leksika1/letopisi.git ~/letopisi && cd ~/letopisi
#   bash infra/stand_setup.sh ~/case.zip      # zip с книгами кейса (необязательно)
#
# Первый запуск создаёт infra/.env и просит вписать YC_API_KEY; второй запуск — разворачивает.
# Повторные запуски безопасны: загрузка в БД пропускает уже существующие записи.
set -euo pipefail
cd "$(dirname "$0")/.."
ZIP=${1:-$HOME/case.zip}

echo "== 1/6 Docker"
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y docker.io docker-compose-v2 python3 curl openssl
    sudo usermod -aG docker "$USER" || true
fi
# Docker Hub из российских облаков часто недоступен — тянем образы через зеркала.
if [[ ! -f /etc/docker/daemon.json ]]; then
    echo '{"registry-mirrors": ["https://mirror.gcr.io", "https://dockerhub.timeweb.cloud"]}' \
        | sudo tee /etc/docker/daemon.json >/dev/null
    sudo systemctl restart docker
fi
DOCKER=(docker)
docker info >/dev/null 2>&1 || DOCKER=(sudo docker)

echo "== 2/6 Настройки (infra/.env)"
ENV_FILE=infra/.env
if [[ ! -f $ENV_FILE ]]; then
    PW=$(openssl rand -hex 16)
    umask 077
    printf '%s\n' \
        "POSTGRES_PASSWORD=$PW" \
        "DATABASE_URL=postgresql+psycopg://letopisi:$PW@postgres:5432/letopisi" \
        "YC_FOLDER_ID=b1gd8e004kbqm557mqlu" \
        "LLM_BACKEND=yandex" \
        "YC_API_KEY=" > "$ENV_FILE"
fi
if ! grep -Eq '^YC_API_KEY=.+' "$ENV_FILE"; then
    echo "Впишите API-ключ после YC_API_KEY= в файле $ENV_FILE (команда: nano $ENV_FILE)"
    echo "и запустите скрипт ещё раз. Без ключа не работает разбор запросов обычными словами."
    exit 1
fi

echo "== 3/6 Результаты ядра для книги 2020"
mkdir -p data/observations data/chunks data/sections data/raw
for f in release/book27_2020/*.jsonl.gz release/book27_2020/*.json.gz; do
    n=$(basename "$f" .gz)
    dir=${n%%_*}
    gunzip -c "$f" > "data/$dir/${n#*_}"
done
python3 -m pipeline.devtools.build_stand_bundle --out data/stand_bundle

echo "== 4/6 Книги (PDF)"
originals=$(python3 infra/import_originals.py "$ZIP")

echo "== 5/6 Сборка и запуск (несколько минут при первом запуске)"
cd infra
"${DOCKER[@]}" compose up -d --build --wait

echo "== 6/6 Загрузка данных"
"${DOCKER[@]}" compose cp ../data/stand_bundle backend:/app/data/
while IFS= read -r name; do
    if [[ -n $name ]]; then
        "${DOCKER[@]}" compose cp "../data/raw/$name" backend:/app/data/raw/
    fi
done <<< "$originals"
"${DOCKER[@]}" compose exec -T backend python pipeline/load.py /app/data/stand_bundle
curl -fsS http://127.0.0.1:8000/api/health && echo
IP=$(curl -fsS -H 'Metadata-Flavor: Google' http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || true)
echo "Готово: http://${IP:-<публичный IP VM>}:8000/"
