"""Export all AED and operator data from PostgreSQL to an Excel workbook.

Usage:
    python scripts/export_defibrillators.py --output defibrylatory.xlsx

The database connection is read from DATABASE_URL. If it is not set, psycopg
uses the standard PostgreSQL environment variables (PGHOST, PGPORT, PGDATABASE,
PGUSER and PGPASSWORD).

Required packages:
    pip install "psycopg[binary]" openpyxl
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
        description="Eksportuje wszystkie dane o defibrylatorach do pliku Excel .xlsx."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("defibrylatory.xlsx"),
        help="Ścieżka pliku wynikowego (domyślnie: defibrylatory.xlsx).",
    )
    return parser.parse_args()


def jsonb_for_excel(value: Any) -> Any:
    """Convert PostgreSQL JSON values to a value accepted by Excel."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def export_to_excel(output_path: Path) -> int:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            'Brak zależności. Zainstaluj je poleceniem: '
            'pip install "psycopg[binary]" openpyxl'
        ) from exc

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError(
            "Brak zależności openpyxl. Zainstaluj ją poleceniem: "
            'pip install openpyxl'
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

    workbook = Workbook()
    workbook.remove(workbook.active)

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)

    def add_sheet(name: str, columns: list[str], rows: list[dict[str, Any]]) -> None:
        sheet = workbook.create_sheet(title=name)
        sheet.append(columns)
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font

        for row in rows:
            sheet.append([jsonb_for_excel(row.get(column)) for column in columns])

        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for index, column in enumerate(columns, start=1):
            max_length = max(
                len(str(sheet.cell(row=row, column=index).value or ""))
                for row in range(1, sheet.max_row + 1)
            )
            sheet.column_dimensions[get_column_letter(index)].width = min(
                max(max_length + 2, 12), 60
            )

    add_sheet("defibrylatory", DEFIBRYLATORY_COLUMNS, defibrylatory)
    add_sheet("operatorzy", OPERATORZY_COLUMNS, operatorzy)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return len(defibrylatory)


def main() -> int:
    args = parse_args()
    if args.output.suffix.lower() != ".xlsx":
        raise SystemExit("Plik wynikowy musi mieć rozszerzenie .xlsx (format Excel).")

    try:
        count = export_to_excel(args.output)
    except Exception as exc:
        raise SystemExit(f"Eksport nie powiódł się: {exc}") from exc

    print(f"Wyeksportowano {count} defibrylatorów do: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
