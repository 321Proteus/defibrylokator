"""Import AED records from json_source/aed_przemysl.json into PostgreSQL.

The source contains addresses but no coordinates, so addresses are geocoded
with the project's existing geocoder before anything is written to the DB.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geocode import forward_geocode  # noqa: E402
from import_aed_helpers import (  # noqa: E402
    availability_from_public_access,
    insert_records,
    load_json,
    make_address,
    text_or_none,
)


SOURCE_PATH = ROOT / "json_source" / "aed_przemysl.json"


def geocode_candidates(address: str) -> list[str]:
    """Return progressively simpler address forms for Nominatim."""
    street, separator, rest = address.strip().partition(",")
    street = re.sub(r"^(ul\.|al\.|pl\.|os\.)\s*", "", street, flags=re.IGNORECASE)
    street = re.sub(r"(\d+)[A-Za-z]?\s*[-/]\s*\d+[A-Za-z]?", r"\1", street)
    cleaned = street + separator + rest
    without_postcode = f"{street}, {re.sub(r'\d{2}-\d{3}', '', rest).strip()}".strip(", ")
    return list(dict.fromkeys((address, cleaned, without_postcode)))


def build_records() -> list[dict]:
    source = load_json(SOURCE_PATH)
    items = source.get("rows") or source.get("data") or source.get("items") or []
    records = []
    geocode_cache: dict[str, tuple[float, float]] = {}

    for item in items:
        properties = item.get("attributes") or item
        source_id = text_or_none(properties.get("id"))
        if source_id is None:
            raise ValueError(f"Rekord Przemyśla nie ma identyfikatora: {item!r}")

        address = make_address(
            None,
            properties.get("street"),
            properties.get("home_number"),
            properties.get("postcode"),
            properties.get("city", "Przemyśl"),
        )
        if address is None:
            raise ValueError(f"Rekord Przemyśla nie ma adresu: {item!r}")

        if address not in geocode_cache:
            coordinates = next(
                (candidate for candidate in map(forward_geocode, geocode_candidates(address)) if candidate is not None),
                None,
            )
            if coordinates is None:
                raise RuntimeError(f"Nie udało się geokodować adresu: {address}")
            geocode_cache[address] = coordinates

        lat, lon = geocode_cache[address]
        records.append(
            {
                "zrodlo": "przemysl",
                "zrodlo_id": source_id,
                "lat": lat,
                "lon": lon,
                "nazwa": text_or_none(properties.get("name")),
                "gdzie_dokladnie": text_or_none(properties.get("name")),
                "pietro": None,
                "wewnatrz": None,
                "adres": address,
                "dostepnosc": availability_from_public_access(
                    properties.get("description")
                ),
                "godziny_otwarcia": None,
                "operator_id": None,
                "telefon": text_or_none(properties.get("phone")),
                "status": None,
                "ostatnia_weryfikacja": None,
                "uwagi": text_or_none(properties.get("description")),
                "zdjecie_url": None,
                "inne": properties,
            }
        )
    return records


if __name__ == "__main__":
    insert_records(build_records())
