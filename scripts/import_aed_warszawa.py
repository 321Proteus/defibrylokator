"""Import AED records from json_source/aed_warszawa.json into PostgreSQL."""

from __future__ import annotations

from pathlib import Path

from import_aed_helpers import (
    availability_from_public_access,
    inside_from_text,
    insert_records,
    load_json,
    make_address,
    text_or_none,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "json_source" / "aed_warszawa.json"


def build_records() -> list[dict]:
    records = []
    for feature in load_json(SOURCE_PATH):
        properties = feature.get("properties") or {}
        coordinates = feature["geometry"]["coordinates"][0]
        source_id = text_or_none(properties.get("defibrillator_id"))
        if source_id is None:
            raise ValueError("Rekord Warszawy nie ma defibrillator_id.")

        records.append(
            {
                "zrodlo": "warszawa",
                "zrodlo_id": source_id,
                "lat": float(coordinates[1]),
                "lon": float(coordinates[0]),
                "nazwa": text_or_none(properties.get("location_object_name")),
                "gdzie_dokladnie": text_or_none(
                    properties.get("location_description")
                ),
                "pietro": None,
                "wewnatrz": inside_from_text(properties.get("device_location")),
                "adres": make_address(
                    None,
                    properties.get("location_street"),
                    properties.get("location_building"),
                    properties.get("location_postcode"),
                    properties.get("location_city"),
                ),
                "dostepnosc": availability_from_public_access(
                    properties.get("device_public_access")
                ),
                "godziny_otwarcia": text_or_none(
                    properties.get("device_access_description")
                ),
                "operator_id": None,
                "telefon": None,
                "status": None,
                "ostatnia_weryfikacja": None,
                "uwagi": None,
                "zdjecie_url": None,
                "inne": properties,
            }
        )
    return records


if __name__ == "__main__":
    insert_records(build_records())

