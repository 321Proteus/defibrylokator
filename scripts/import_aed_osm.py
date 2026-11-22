"""Import AED records from an OpenAEDMap / OpenStreetMap GeoJSON export (PL.geojson) into PostgreSQL.

    python scripts/import_aed_osm.py                     # ./PL.geojson -> DB from web/.env or DATABASE_URL
    python scripts/import_aed_osm.py path/to/PL.geojson
    python scripts/import_aed_osm.py --dry-run           # parse and report only, no DB access

Records are keyed by the OSM element (zrodlo='osm', zrodlo_id='node/123'), so re-running with a newer
export updates existing rows. OSM elements removed from a newer export are NOT deleted.
Every original OSM tag is kept in the `inne` JSON column.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from import_aed_helpers import (  # noqa: E402  (also puts web/ on sys.path)
    insert_records,
    load_json,
    make_address,
    text_or_none,
)
import aed_db  # noqa: E402

SOURCE = "osm"
DEFAULT_PATH = ROOT / "PL.geojson"

# schema CHECK constraints on defibrylatory
LAT_RANGE = (49.0, 55.0)
LON_RANGE = (14.1, 24.2)
VERIFICATION_RE = re.compile(r"^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$")

# OSM `access` tag -> dostepnosc
ACCESS_MAP = {
    "yes": "publiczna",
    "public": "publiczna",
    "customers": "klienci",
    "permissive": "dozwolona",
    "permit": "zezwolenie",
    "private": "prywatna",
    "no": "prywatna",
}
INDOOR_MAP = {"yes": True, "no": False}


def first_text(props: dict, *keys: str) -> str | None:
    for key in keys:
        value = text_or_none(props.get(key))
        if value:
            return value
    return None


def verification_date(props: dict) -> str | None:
    value = first_text(props, "check_date:defibrillator", "check_date")
    return value if value and VERIFICATION_RE.match(value) else None


def photo_url(props: dict) -> str | None:
    value = text_or_none(props.get("image"))
    return value if value and value.lower().startswith(("http://", "https://")) else None


def notes(props: dict) -> str | None:
    parts = [text_or_none(props.get("note")), text_or_none(props.get("fixme"))]
    fixme = parts[1]
    if fixme:
        parts[1] = f"fixme: {fixme}"
    return " | ".join(p for p in parts if p) or None


def build_record(feature: dict) -> dict[str, Any] | None:
    """Map one GeoJSON feature to a defibrylatory row, or None if it can't be stored."""
    props = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    if geometry.get("type") != "Point":
        return None
    osm_id, osm_type = props.get("@osm_id"), props.get("@osm_type") or "node"
    if osm_id is None:
        return None
    try:
        lon, lat = float(geometry["coordinates"][0]), float(geometry["coordinates"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        return None

    return {
        "zrodlo": SOURCE,
        "zrodlo_id": f"{osm_type}/{osm_id}",
        "lat": lat,
        "lon": lon,
        "nazwa": first_text(props, "name", "name:pl"),
        "gdzie_dokladnie": first_text(
            props,
            "defibrillator:location:pl",
            "defibrillator:location",
            "defibrillator:location:en",
            "description:pl",
            "description",
            "description:en",
        ),
        "pietro": first_text(props, "level"),
        "wewnatrz": INDOOR_MAP.get((text_or_none(props.get("indoor")) or "").lower()),
        "adres": make_address(
            None,
            props.get("addr:street"),
            props.get("addr:housenumber"),
            props.get("addr:postcode"),
            props.get("addr:city"),
        ),
        "dostepnosc": ACCESS_MAP.get((text_or_none(props.get("access")) or "").lower(), "nieznana"),
        "godziny_otwarcia": text_or_none(props.get("opening_hours")),
        "operator_id": None,  # filled in by resolve_operators()
        "telefon": first_text(props, "emergency:phone", "phone", "contact:phone"),
        "status": None,
        "ostatnia_weryfikacja": verification_date(props),
        "uwagi": notes(props),
        "zdjecie_url": photo_url(props),
        "inne": props,
        "_operator": first_text(props, "operator", "operator:short"),
        "_operator_wikidata": text_or_none(props.get("operator:wikidata")),
    }


def build_records(path: Path) -> tuple[list[dict], Counter]:
    data = load_json(path)
    stats: Counter = Counter()
    records: dict[str, dict] = {}
    for feature in data.get("features", []):
        record = build_record(feature)
        if record is None:
            stats["skipped"] += 1
            continue
        if record["zrodlo_id"] in records:
            stats["duplicate_in_file"] += 1
        records[record["zrodlo_id"]] = record
    stats["records"] = len(records)
    return list(records.values()), stats


def resolve_operators(records: list[dict]) -> int:
    """Find or create `operatorzy` rows for operator names and set operator_id on the records."""
    wanted: dict[str, tuple[str, str | None]] = {}  # normalized name -> (display name, wikidata)
    for record in records:
        name = record.get("_operator")
        if name:
            wanted.setdefault(name.casefold(), (name, record.get("_operator_wikidata")))

    with aed_db._connect() as conn, conn.cursor() as cur:
        existing = {
            nazwa.strip().casefold(): operator_id
            for operator_id, nazwa in cur.execute("SELECT operator_id, nazwa FROM operatorzy")
        }
        created = 0
        for key, (name, wikidata) in wanted.items():
            if key not in existing:
                existing[key] = cur.execute(
                    "INSERT INTO operatorzy (nazwa, wikidata) VALUES (%s, %s) RETURNING operator_id",
                    (name, wikidata),
                ).fetchone()[0]
                created += 1

    for record in records:
        name = record.get("_operator")
        record["operator_id"] = existing[name.casefold()] if name else None
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH, help="GeoJSON file (default: ./PL.geojson)")
    parser.add_argument("--dry-run", action="store_true", help="parse and report only; do not touch the database")
    args = parser.parse_args()

    if not args.path.exists():
        sys.exit(f"Nie znaleziono pliku: {args.path}")

    records, stats = build_records(args.path)
    availability = Counter(r["dostepnosc"] for r in records)
    print(f"Wczytano {stats['records']} rekordów z {args.path.name} "
          f"(pominięto {stats['skipped']} bez poprawnych współrzędnych/ID, "
          f"{stats['duplicate_in_file']} duplikatów w pliku).")
    print("Dostępność:", dict(availability))

    if args.dry_run:
        print("--dry-run: baza nie została zmieniona.")
        return

    created = resolve_operators(records)
    print(f"Operatorzy: dodano {created} nowych.")
    for record in records:
        record.pop("_operator", None)
        record.pop("_operator_wikidata", None)
    insert_records(records)


if __name__ == "__main__":
    main()
