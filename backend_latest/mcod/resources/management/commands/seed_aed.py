"""Publishes the AED register (master CSV from the `web/` app) as a map-ready resource.

    docker exec -i mcod-admin python manage.py seed_aed - < web/master_aed_dane_gov.csv

The CSV is the Defibrylokator standard schema (see AED_COLUMNS). Rows without valid coordinates are
skipped. The resource gets a `tabular_data_schema` with the `geo` mapping (label / latitude / longitude)
already set, so once the usual file processing finishes, MCOD indexes the points and serves them at
/resources/{id}/geo (the same result as picking the columns in the admin's "Maps and plots" section).
Running it again replaces the file of the existing resource instead of creating a duplicate.
"""

import csv
import io
import sys
import time

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from mcod.categories.models import Category
from mcod.datasets.models import Dataset
from mcod.organizations.models import Organization
from mcod.resources.models import Resource, ResourceFile
from mcod.users.models import User

AED_COLUMNS = [
    "id_zewnetrzne",
    "nazwa_obiektu",
    "miasto",
    "kod_pocztowy",
    "ulica_i_numer",
    "opis_lokalizacji",
    "szerokosc_geo",
    "dlugosc_geo",
    "dostepnosc",
    "godziny_otwarcia",
    "status",
    "dysponent",
    "kontakt_telefon",
]
NUMERIC_COLUMNS = {"szerokosc_geo", "dlugosc_geo"}
GEO_COLUMNS = {"nazwa_obiektu": "label", "szerokosc_geo": "b", "dlugosc_geo": "l"}
LAT_RANGE = (49.0, 54.9)
LON_RANGE = (14.1, 24.2)


def build_schema():
    """Table Schema with the `geo` mapping; the same shape the admin's map widget produces."""
    fields, geo = [], {}
    for index, name in enumerate(AED_COLUMNS):
        field = {"name": name, "type": "number" if name in NUMERIC_COLUMNS else "string", "format": "default"}
        if name in GEO_COLUMNS:
            field["geo"] = GEO_COLUMNS[name]
            geo[GEO_COLUMNS[name]] = {"col_name": name, "col_index": index}
        fields.append(field)
    return {"fields": fields, "missingValues": [], "geo": geo}


def clean_rows(text):
    """Returns (rows with valid coordinates, number of skipped rows)."""
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    missing = [c for c in AED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise CommandError(f"The CSV is missing columns: {', '.join(missing)}")
    rows, skipped = [], 0
    for row in reader:
        try:
            lat, lon = float(row["szerokosc_geo"].replace(",", ".")), float(row["dlugosc_geo"].replace(",", "."))
        except ValueError:
            skipped += 1
            continue
        if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
            skipped += 1
            continue
        row["szerokosc_geo"], row["dlugosc_geo"] = repr(lat), repr(lon)
        rows.append({c: row[c] for c in AED_COLUMNS})
    return rows, skipped


class Command(BaseCommand):
    help = "Publish the AED register (master CSV) as a map-ready MCOD resource."

    def add_arguments(self, parser):
        parser.add_argument("csv", help="Path to the master CSV (';'-separated), or '-' for stdin.")
        parser.add_argument("--organization", default="Polska Baza AED")
        parser.add_argument("--dataset", default="Rejestr defibrylatorów AED")
        parser.add_argument("--resource", default="Lokalizacje defibrylatorów AED")
        parser.add_argument("--wait", type=int, default=120, help="Seconds to wait for processing (0 = do not wait).")

    def handle(self, *args, **opts):
        raw = sys.stdin.buffer.read() if opts["csv"] == "-" else open(opts["csv"], "rb").read()
        rows, skipped = clean_rows(raw.decode("utf-8-sig"))
        if not rows:
            raise CommandError("No rows with valid coordinates to publish.")
        if skipped:
            self.stderr.write(f"Skipped {skipped} row(s) without valid coordinates.")

        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=AED_COLUMNS, delimiter=";", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        content = out.getvalue().encode("utf-8")

        admin = User.objects.filter(is_superuser=True).order_by("id").first()
        if admin is None:
            raise CommandError("No superuser found (run init_mcod_db first).")

        org, _ = Organization.objects.get_or_create(
            title=opts["organization"],
            defaults={
                "institution_type": Organization.INSTITUTION_TYPE_STATE,
                "description": "Zbiorczy rejestr lokalizacji defibrylatorów AED w Polsce.",
            },
        )
        dataset, _ = Dataset.objects.get_or_create(
            title=opts["dataset"],
            organization=org,
            defaults={
                "notes": "Ogólnopolski rejestr lokalizacji publicznie dostępnych defibrylatorów AED zebrany od wielu dysponentów.",
                "update_frequency": "daily",
                "category": Category.objects.filter(title__icontains="zdrow").first() or Category.objects.first(),
                "created_by": admin,
                "modified_by": admin,
                "status": "published",
            },
        )

        resource = Resource.objects.filter(dataset=dataset, title=opts["resource"]).first()
        file_name = f"aed_register_{timezone.now():%Y%m%d%H%M%S}.csv"
        if resource is None:
            resource = Resource.objects.create(
                title=opts["resource"],
                description="Lokalizacje defibrylatorów AED: adres, współrzędne, dostępność i dysponent.",
                dataset=dataset,
                type="file",
                format="csv",
                status="published",
                language="pl",
                data_date=timezone.now().date(),
                tabular_data_schema=build_schema(),
                created_by=admin,
                modified_by=admin,
            )
            ResourceFile.objects.create(resource=resource, is_main=True, format="csv", file=ContentFile(content, name=file_name))
            action = "created"
        else:
            resource.tabular_data_schema = build_schema()
            resource.data_date = timezone.now().date()
            resource.modified_by = admin
            resource.save()
            ResourceFile.objects.update_or_create(
                resource=resource, is_main=True, defaults={"format": "csv", "file": ContentFile(content, name=file_name)}
            )
            resource.revalidate()
            action = "updated"

        self.stdout.write(f"Resource {action}: id={resource.id}, {len(rows)} row(s). API: /resources/{resource.id}/geo")
        self._wait(resource.id, opts["wait"])

    def _wait(self, resource_id, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            res = Resource.raw.get(id=resource_id)
            if res.data_tasks_last_status in ("SUCCESS", "FAILURE"):
                self.stdout.write(
                    f"Processing: {res.data_tasks_last_status}, has_map={res.has_map}, has_table={res.has_table}"
                )
                return
            time.sleep(3)
        self.stdout.write("Processing not finished yet; check the admin (task results) later.")
