#!/usr/bin/env bash
# Option:
# --no-build skip the docker image build
# --backend  start only the backend
# --reseed   re-publish web/master_aed_dane_gov.csv as the portal's AED resource (otherwise done once)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend_latest"
FRONTEND="$ROOT/frontend_latest"
STATE="$ROOT/.initialized"
DC=(docker compose -f docker-compose.local.yml)

BUILD=1; FRONT=1; RESEED=0
for a in "$@"; do
  case "$a" in
    --no-build) BUILD=0 ;;
    --backend) FRONT=0 ;;
    --reseed) RESEED=1 ;;
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

# Publish the AED register as a map-ready portal resource (needs celery + Elasticsearch running).
# Source: web/master_aed_dane_gov.csv (web app running without a database), else the web app's CSV export
# at $AED_CSV_URL, e.g. AED_CSV_URL=https://<your-app>/dane-gov-export.csv ./run.sh --reseed
# The resource id is handed to the frontend through assets/aed/config.json.
SEED_CSV="$ROOT/web/master_aed_dane_gov.csv"
SEED_TARGET="$BACKEND/data/aed_master.csv"   # ./data is mounted into the containers
if [ "$RESEED" = 1 ] || ! grep -qx seed "$STATE"; then
  HAVE_SEED=0
  mkdir -p "$BACKEND/data"
  if [ -f "$SEED_CSV" ]; then
    cp "$SEED_CSV" "$SEED_TARGET" && HAVE_SEED=1
  elif [ -n "${AED_CSV_URL:-}" ]; then
    echo ">> Downloading the AED register from $AED_CSV_URL"
    if curl -fsSL "$AED_CSV_URL" -o "$SEED_TARGET"; then HAVE_SEED=1; else echo "!! Could not download $AED_CSV_URL" >&2; fi
  else
    echo "-- No AED data to publish: set AED_CSV_URL to the web app's /dane-gov-export.csv (or create $SEED_CSV), then run with --reseed."
  fi

  if [ "$HAVE_SEED" = 1 ]; then
    echo ">> Publishing the AED register as a portal resource"
    SEED_LOG="$(mktemp)"
    if "${DC[@]}" run --rm -T --no-deps mcod-local-admin python manage.py seed_aed /usr/src/mcod_backend/data/aed_master.csv > "$SEED_LOG" 2>&1; then
      grep -E "^(Resource|Processing|Skipped)" "$SEED_LOG" || true
      RES_ID="$(sed -n 's/^Resource [a-z]*: id=\([0-9]*\).*/\1/p' "$SEED_LOG" | tail -1)"
      if [ -n "$RES_ID" ]; then
        mkdir -p "$FRONTEND/src/assets/aed"
        printf '{"resourceId": "%s"}\n' "$RES_ID" > "$FRONTEND/src/assets/aed/config.json"
      fi
      grep -qx seed "$STATE" || echo seed >> "$STATE"
    else
      tail -n 20 "$SEED_LOG" >&2
      echo "!! Publishing the AED register failed; the map will show sample data. Retry with --reseed." >&2
    fi
    rm -f "$SEED_LOG"
  fi
fi

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
