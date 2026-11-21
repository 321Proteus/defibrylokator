"""Shared database helpers for the source-specific AED import scripts."""

from __future__ import annotations

import json
import os
from typing import Any


INSERT_COLUMNS = (
    "zrodlo",
    "zrodlo_id",
    "lat",
    "lon",
    "nazwa",
    "gdzie_dokladnie",
    "pietro",
    "wewnatrz",
    "adres",
    "dostepnosc",
    "godziny_otwarcia",
    "operator_id",
    "telefon",
    "status",
    "ostatnia_weryfikacja",
    "uwagi",
    "zdjecie_url",
    "inne",
)

INSERT_SQL = f"""
    INSERT INTO defibrylatory ({", ".join(INSERT_COLUMNS)})
    VALUES ({", ".join(f"%({column})s" for column in INSERT_COLUMNS)})
    ON CONFLICT (zrodlo, zrodlo_id) DO UPDATE SET
        lat = EXCLUDED.lat,
        lon = EXCLUDED.lon,
        nazwa = EXCLUDED.nazwa,
        gdzie_dokladnie = EXCLUDED.gdzie_dokladnie,
        pietro = EXCLUDED.pietro,
        wewnatrz = EXCLUDED.wewnatrz,
        adres = EXCLUDED.adres,
        dostepnosc = EXCLUDED.dostepnosc,
        godziny_otwarcia = EXCLUDED.godziny_otwarcia,
        operator_id = EXCLUDED.operator_id,
        telefon = EXCLUDED.telefon,
        status = EXCLUDED.status,
        ostatnia_weryfikacja = EXCLUDED.ostatnia_weryfikacja,
        uwagi = EXCLUDED.uwagi,
        zdjecie_url = EXCLUDED.zdjecie_url,
        inne = EXCLUDED.inne
"""


def load_json(path: Any) -> Any:
    with open(path, "r", encoding="utf-8") as source_file:
        return json.load(source_file)


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def make_address(
    street_prefix: Any,
    street: Any,
    building: Any,
    postcode: Any,
    city: Any,
) -> str | None:
    street_part = " ".join(
        part
        for part in (
            text_or_none(street_prefix),
            text_or_none(street),
            text_or_none(building),
        )
        if part
    )
    city_part = " ".join(
        part for part in (text_or_none(postcode), text_or_none(city)) if part
    )
    return ", ".join(part for part in (street_part, city_part) if part) or None


def inside_from_text(value: Any) -> bool | None:
    text = (text_or_none(value) or "").casefold()
    if "wewn" in text:
        return True
    if "zewn" in text:
        return False
    return None


def availability_from_public_access(value: Any) -> str:
    text = (text_or_none(value) or "").casefold()
    if "publicz" in text:
        return "publiczna"
    if "prywat" in text:
        return "prywatna"
    if "klient" in text:
        return "klienci"
    return "nieznana"


def insert_records(records: list[dict[str, Any]]) -> int:
    if not records:
        print("Nie znaleziono rekordów do importu.")
        return 0

    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError(
            'Brak zależności. Zainstaluj ją poleceniem: '
            'pip install "psycopg[binary]"'
        ) from exc

    params = []
    for record in records:
        row = {column: record.get(column) for column in INSERT_COLUMNS}
        row["inne"] = Jsonb(row["inne"] or {})
        params.append(row)

    database_url = os.environ.get("DATABASE_URL")
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(INSERT_SQL, params)
        connection.commit()

    print(f"Zaimportowano lub zaktualizowano {len(records)} rekordów.")
    return len(records)

