"""Import AED records from json_source/aed_sopot.json into PostgreSQL.

The source contains addresses but no coordinates, so addresses are geocoded
with the project's existing geocoder before anything is written to the DB.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geocode import forward_geocode  # noqa: E402
from import_aed_helpers import (  # noqa: E402
    insert_records,
    load_json,
    make_address,
    text_or_none,
)


SOURCE_PATH = ROOT / "json_source" / "aed_sopot.json"


def field(item: dict, *names: str):
    """Read a field while accepting the source's mojibake key spelling."""
    for name in names:
        if name in item:
            return item[name]
    return None


def build_records() -> list[dict]:
    source = load_json(SOURCE_PATH)
    records = []
    geocode_cache: dict[str, tuple[float, float]] = {}

    for item in source:
        address = make_address(
            field(item, "ul."),
            field(item, "Nazwa ulicy"),
            field(item, "Numer"),
            field(item, "Kod PNA"),
            field(item, "Miejscowość", "MiejscowoĹ›Ä‡"),
        )
        if address is None:
            raise ValueError(f"Rekord Sopotu nie ma adresu: {item!r}")

        if address not in geocode_cache:
            coordinates = forward_geocode(address)
            if coordinates is None:
                raise RuntimeError(f"Nie udało się geokodować adresu: {address}")
            geocode_cache[address] = coordinates

        lat, lon = geocode_cache[address]
        quantity = int(field(item, "Ilość", "IloĹ›Ä‡") or 1)
        source_row_id = text_or_none(field(item, "l.p."))
        if source_row_id is None:
            raise ValueError("Rekord Sopotu nie ma numeru l.p.")

        for device_number in range(1, quantity + 1):
            records.append(
                {
                    "zrodlo": "sopot",
                    "zrodlo_id": f"{source_row_id}:{device_number}",
                    "lat": lat,
                    "lon": lon,
                    "nazwa": text_or_none(field(item, "Nazwa")),
                    "gdzie_dokladnie": text_or_none(field(item, "Lokalizacja")),
                    "pietro": None,
                    "wewnatrz": None,
                    "adres": address,
                    "dostepnosc": "nieznana",
                    "godziny_otwarcia": text_or_none(
                        field(
                            item,
                            "Godziny dostępności",
                            "Godziny dotÄ™pnoĹ›ci",
                        )
                    ),
                    "operator_id": None,
                    "telefon": None,
                    "status": None,
                    "ostatnia_weryfikacja": None,
                    "uwagi": None,
                    "zdjecie_url": None,
                    "inne": {
                        **item,
                        "liczba_zrodla": quantity,
                        "numer_urzadzenia_w_zrodle": device_number,
                    },
                }
            )
    return records


if __name__ == "__main__":
    insert_records(build_records())

