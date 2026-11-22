"""Import AED records from json_source/aed_krosno.json into PostgreSQL."""

from __future__ import annotations

from pathlib import Path

from import_aed_helpers import insert_records, load_json, text_or_none


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "json_source" / "aed_krosno.json"


def source_value(attributes: dict, column: str):
    value = attributes.get(column)
    if isinstance(value, dict):
        value = value.get("val", value.get("repr"))
    if column == "col6" and isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def build_records() -> list[dict]:
    source = load_json(SOURCE_PATH)
    records = []

    for item in source.get("data", []):
        attributes = item.get("attributes") or {}
        source_id = text_or_none(source_value(attributes, "col6"))
        if source_id is None:
            source_id = text_or_none(item.get("id"))
        if source_id is None:
            raise ValueError(f"Rekord Krosna nie ma identyfikatora: {item!r}")

        longitude = source_value(attributes, "col1")
        latitude = source_value(attributes, "col2")
        if longitude is None or latitude is None:
            raise ValueError(f"Rekord Krosna nie ma współrzędnych: {item!r}")

        location = text_or_none(source_value(attributes, "col4"))
        records.append(
            {
                "zrodlo": "krosno",
                "zrodlo_id": source_id,
                "lat": float(latitude),
                "lon": float(longitude),
                "nazwa": text_or_none(source_value(attributes, "col3")),
                "gdzie_dokladnie": location,
                "pietro": None,
                "wewnatrz": None,
                "adres": text_or_none(source_value(attributes, "col5")),
                "dostepnosc": "nieznana",
                "godziny_otwarcia": None,
                "operator_id": None,
                "telefon": None,
                "status": None,
                "ostatnia_weryfikacja": None,
                "uwagi": None,
                "zdjecie_url": None,
                "inne": item,
            }
        )
    return records


if __name__ == "__main__":
    insert_records(build_records())
