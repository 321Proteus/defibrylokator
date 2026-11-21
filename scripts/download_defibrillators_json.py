"""Export all AED and operator data from PostgreSQL to a JSON file.

Usage:
    python scripts/download_defibrillators_json.py --output defibrylatory.json

The database connection is read from DATABASE_URL. If it is not set, psycopg
uses the standard PostgreSQL environment variables (PGHOST, PGPORT, PGDATABASE,
PGUSER and PGPASSWORD).

Required package:
    pip install "psycopg[binary]"
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


DEFIBRYLATORY_COLUMNS = [
    "defibrylator_id",
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
    "operator_nazwa",
    "operator_telefon",
    "operator_wikidata",
]

OPERATORZY_COLUMNS = ["operator_id", "nazwa", "telefon", "wikidata"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Eksportuje wszystkie dane o defibrylatorach do pliku JSON."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("defibrylatory.json"),
        help="Ścieżka pliku wynikowego (domyślnie: defibrylatory.json).",
    )
    return parser.parse_args()


def export_to_json(output_path: Path) -> int:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            'Brak zależności. Zainstaluj ją poleceniem: '
            'pip install "psycopg[binary]"'
        ) from exc

    defibrylator_select = ",\n            ".join(
        f"d.{column}" for column in DEFIBRYLATORY_COLUMNS[:-3]
    )
    query_defibrylatory = f"""
        SELECT
            {defibrylator_select},
            o.nazwa AS operator_nazwa,
            o.telefon AS operator_telefon,
            o.wikidata AS operator_wikidata
        FROM defibrylatory AS d
        LEFT JOIN operatorzy AS o ON o.operator_id = d.operator_id
        ORDER BY d.defibrylator_id
    """
    query_operatorzy = """
        SELECT operator_id, nazwa, telefon, wikidata
        FROM operatorzy
        ORDER BY operator_id
    """

    database_url = os.environ.get("DATABASE_URL")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query_defibrylatory)
            defibrylatory = cursor.fetchall()
            cursor.execute(query_operatorzy)
            operatorzy = cursor.fetchall()

    export = {
        "defibrylatory": defibrylatory,
        "operatorzy": operatorzy,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(export, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return len(defibrylatory)


def main() -> int:
    args = parse_args()
    if args.output.suffix.lower() != ".json":
        raise SystemExit("Plik wynikowy musi mieć rozszerzenie .json.")

    try:
        count = export_to_json(args.output)
    except Exception as exc:
        raise SystemExit(f"Eksport nie powiódł się: {exc}") from exc

    print(f"Wyeksportowano {count} defibrylatorów do: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
