#!/usr/bin/env bash
# Force an MCOD Data Source import right now (instead of waiting for the daily schedule).
# Usage: scripts/force_mcod_import.sh [data_source_id]   (default: the AED source, id 5)
# Needs the MCOD Docker stack running (mcod-admin, mcod-celery) and the web app's feed reachable.
set -euo pipefail
ID="${1:-5}"

docker exec -i mcod-admin python manage.py shell <<PY 2>&1 | grep '^OUT'
from mcod.harvester.tasks import import_data_task
r = import_data_task.apply_async(args=[$ID], kwargs={"force": True})
print("OUT queued task", r.id)
PY

# MCOD only re-downloads a link resource when its metadata changed; make sure the file is refreshed.
sleep 20
docker exec -i mcod-admin python manage.py shell <<PY 2>&1 | grep '^OUT'
from mcod.resources.models import Resource
for r in Resource.raw.filter(dataset__source_id=$ID):
    r.revalidate()
    print("OUT re-downloading file of resource", r.id)
PY

echo "Watch progress: docker logs -f mcod-celery   (look for 'Starting import_data method')"
sleep 40
docker exec -i mcod-admin python manage.py shell <<PY 2>&1 | grep '^OUT'
from mcod.harvester.models import DataSource
ds = DataSource.objects.get(id=$ID)
print("OUT last import:", ds.last_import_status, ds.last_import_timestamp, "| next scheduled:", ds.next_import_date)
PY
