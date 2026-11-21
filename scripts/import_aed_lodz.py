"""Import AED records from json_source/aed_lodz.json into PostgreSQL."""

from __future__ import annotations

from pathlib import Path

from import_aed_helpers import (
    inside_from_text,
    insert_records,
    load_json,
    text_or_none,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "json_source" / "aed_lodz.json"


def build_records() -> list[dict]:
    source = load_json(SOURCE_PATH)
    records = []
    for feature in source.get("features", []):
        attributes = feature.get("attributes") or {}
        geometry = feature.get("geometry") or {}
        source_id = attributes.get("OBJECTID")
        if source_id is None:
            raise ValueError("Rekord Łodzi nie ma OBJECTID.")

        records.append(
            {
                "zrodlo": "lodz",
                "zrodlo_id": str(source_id),
                "lat": float(geometry["y"]),
                "lon": float(geometry["x"]),
                "nazwa": text_or_none(attributes.get("OBIEKT")),
                "gdzie_dokladnie": text_or_none(attributes.get("LOK_OB")),
                "pietro": None,
                "wewnatrz": inside_from_text(attributes.get("LOK_OB")),
                "adres": text_or_none(attributes.get("ADRES")),
                "dostepnosc": "nieznana",
                "godziny_otwarcia": None,
                "operator_id": None,
                "telefon": None,
                "status": None,
                "ostatnia_weryfikacja": None,
                "uwagi": None,
                "zdjecie_url": None,
                "inne": attributes,
            }
        )
    return records


if __name__ == "__main__":
    insert_records(build_records())

