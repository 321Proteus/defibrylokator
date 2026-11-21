#!/usr/bin/env bash
# Option:
# --no-build skip the docker image build
# --backend  start only the backend
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend_latest"
FRONTEND="$ROOT/frontend_latest"
STATE="$ROOT/.initialized"
DC=(docker compose -f docker-compose.local.yml)

BUILD=1; FRONT=1
for a in "$@"; do
  case "$a" in
    --no-build) BUILD=0 ;;
    --backend) FRONT=0 ;;
    *) echo "Unknown option: $a" >&2; exit 1 ;;
  esac
done

for cmd in docker node npm curl; do
  command -v "$cmd" >/dev/null || { echo "Missing required tool: $cmd" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker daemon is not running." >&2; exit 1; }

# config files
cd "$BACKEND"
if [ ! -f .env ]; then
  echo ">> Creating backend .env"
  cp env.sample .env

  KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  sed -i.bak "s/^FIELD_ENCRYPTION_KEYS=.*/FIELD_ENCRYPTION_KEYS=$KEY/" .env

  # Discourse image lives in a private registry; disable the forum by default.
  sed -i.bak "s/^DISCOURSE_FORUM_ENABLED=.*/DISCOURSE_FORUM_ENABLED=false/" .env
  rm -f .env.bak
fi
[ -f mcod/settings/local.py ] || { echo ">> Creating mcod/settings/local.py"; cp mcod/settings/local.py.sample mcod/settings/local.py; }

if ! grep -q "api.mcod.local" /etc/hosts; then
  echo "!! /etc/hosts is missing the mcod.local entries. Run once:" >&2
  echo "   echo '127.0.0.1 mcod.local api.mcod.local admin.mcod.local cms.mcod.local forum.mcod.local mail.mcod.local' | sudo tee -a /etc/hosts" >&2
  exit 1
fi

# backend
if [ "$BUILD" = 1 ]; then
  echo ">> Building backend images (can take a long time)"
  "${DC[@]}" build
fi

echo ">> Starting dependencies"
docker compose up -d mcod-db mcod-elasticsearch mcod-rabbitmq mcod-redis mcod-rdfdb mcod-smtp mcod-nginx

echo ">> Waiting for Postgres and Elasticsearch"
until docker exec mcod-db pg_isready -U mcod >/dev/null 2>&1; do sleep 2; done
until curl -fs "http://localhost:9200/_cluster/health" >/dev/null 2>&1; do sleep 3; done

# Each first-run step is recorded separately in $STATE, so a failure in a later
# step doesn't make a retry repeat earlier ones (loaddata is not re-runnable).
touch "$STATE"
if ! grep -qx db "$STATE"; then
  echo ">> First run: migrations + initial data"
  "${DC[@]}" run --rm --no-deps mcod-local-admin python manage.py init_mcod_db
  echo db >> "$STATE"
fi
if ! grep -qx es "$STATE"; then
  echo ">> First run: building Elasticsearch indexes"
  "${DC[@]}" run --rm --no-deps mcod-local-admin python manage.py search_index --rebuild -f
  echo es >> "$STATE"
fi

echo ">> Starting backend services"
"${DC[@]}" up -d mcod-local-api mcod-local-admin mcod-local-cms mcod-local-celery

echo
echo "Backend:  https://api.mcod.local  (docs: /doc)"
echo "Admin:    https://admin.mcod.local  (admin@mcod.local / testadmin123!)"
echo "Stop:     (cd backend_latest && ${DC[*]} down)"
echo

# frontend 
if [ "$FRONT" = 1 ]; then
  cd "$FRONTEND"
  [ -d node_modules ] || { echo ">> npm install"; npm install; }
  echo ">> Starting frontend on http://localhost:4200"
  exec npm run start:local
fi
