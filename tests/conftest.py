"""Shared fixtures. Tests must never touch a real database: credentials from web/.env are ignored."""

import os
import sys
import uuid
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("web", "scripts", ""):
    sys.path.insert(0, str(ROOT / sub) if sub else str(ROOT))

os.environ["AED_SKIP_DOTENV"] = "1"  # before aed_db is imported anywhere

DB_ENV_VARS = ("DATABASE_URL", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_SSLMODE", "PUBLIC_BASE_URL")

HEADERS = [
    "id_zewnetrzne", "nazwa_obiektu", "miasto", "kod_pocztowy", "ulica_i_numer", "opis_lokalizacji",
    "szerokosc_geo", "dlugosc_geo", "dostepnosc", "godziny_otwarcia", "status", "dysponent", "kontakt_telefon",
]


@pytest.fixture(autouse=True)
def no_real_database(monkeypatch):
    for name in DB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def good_row(name="Urząd Gminy", **overrides):
    row = dict(
        id_zewnetrzne=None, nazwa_obiektu=name, miasto="Łódź", kod_pocztowy="90-001",
        ulica_i_numer="ul. Piotrkowska 1", opis_lokalizacji="Parter przy recepcji",
        szerokosc_geo=51.76, dlugosc_geo=19.45, dostepnosc="24/7", godziny_otwarcia=None,
        status="sprawny", dysponent="Miasto Łódź", kontakt_telefon=None,
    )
    row.update(overrides)
    return row


@pytest.fixture
def make_xlsx(tmp_path):
    """make_xlsx(rows, headers=HEADERS, name='f.xlsx') -> path; rows are dicts keyed by HEADERS (or raw lists)."""

    def build(rows, headers=None, name="upload.xlsx", keys=None):
        headers = headers if headers is not None else HEADERS
        keys = keys if keys is not None else headers
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for row in rows:
            ws.append(row if isinstance(row, (list, tuple)) else [row.get(k) for k in keys])
        path = tmp_path / name
        wb.save(path)
        return str(path)

    return build


@pytest.fixture
def good():
    return good_row


TEST_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_URL, reason="set TEST_DATABASE_URL to run database tests")


@pytest.fixture
def db(monkeypatch):
    """A throw-away schema (tables from schema.py) on TEST_DATABASE_URL; DATABASE_URL points at it for the test."""
    import psycopg
    import aed_db
    from schema import SCHEMA_SQL

    schema = f"test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(TEST_URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
        sep = "&" if "?" in TEST_URL else "?"
        url = f"{TEST_URL}{sep}options=-csearch_path%3D{schema}"
        try:
            with psycopg.connect(url, autocommit=True) as conn:
                conn.execute(SCHEMA_SQL)
            monkeypatch.setenv("DATABASE_URL", url)
            aed_db._invalidate_cache()
            yield url
        finally:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")
            aed_db._invalidate_cache()
