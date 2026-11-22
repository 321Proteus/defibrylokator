"""Postgres storage for AED submissions from the web form (single + Excel batch).

Active when DATABASE_URL is set; otherwise main.py falls back to the master CSV.
Records use the web "standard schema" (CSV column names) and are mapped onto the
`defibrylatory` table, the same one the scripts/import_aed_*.py importers fill.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterable

SOURCE = "zgloszenia"  # zrodlo for records submitted through the web app

# web form -> DB CHECK values for `dostepnosc`
AVAILABILITY_MAP = {
    "24/7": "publiczna",
    "godziny_otwarcia": "publiczna",
    "ograniczona": "zezwolenie",
}

LIST_SQL = """
    SELECT d.zrodlo, d.zrodlo_id, d.nazwa, d.adres, d.gdzie_dokladnie, d.lat, d.lon,
           d.dostepnosc, d.godziny_otwarcia, d.status, o.nazwa AS operator,
           COALESCE(d.telefon, o.telefon) AS telefon
    FROM defibrylatory d
    LEFT JOIN operatorzy o USING (operator_id)
"""

UPSERT_SQL = """
    INSERT INTO defibrylatory
        (zrodlo, zrodlo_id, lat, lon, nazwa, gdzie_dokladnie, adres, dostepnosc,
         godziny_otwarcia, operator_id, telefon, status)
    VALUES
        (%(zrodlo)s, %(zrodlo_id)s, %(lat)s, %(lon)s, %(nazwa)s, %(gdzie_dokladnie)s, %(adres)s,
         %(dostepnosc)s, %(godziny_otwarcia)s, %(operator_id)s, %(telefon)s, %(status)s)
    ON CONFLICT (zrodlo, zrodlo_id) DO UPDATE SET
        lat = EXCLUDED.lat, lon = EXCLUDED.lon, nazwa = EXCLUDED.nazwa,
        gdzie_dokladnie = EXCLUDED.gdzie_dokladnie, adres = EXCLUDED.adres,
        dostepnosc = EXCLUDED.dostepnosc, godziny_otwarcia = EXCLUDED.godziny_otwarcia,
        operator_id = EXCLUDED.operator_id, telefon = EXCLUDED.telefon, status = EXCLUDED.status
"""


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=VALUE lines); real environment variables win."""
    if path is None and os.environ.get("AED_SKIP_DOTENV"):
        return
    path = path or Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


load_dotenv()


def enabled() -> bool:
    return bool(os.environ.get("DATABASE_URL") or os.environ.get("DB_HOST"))


def _connect():
    """DATABASE_URL, or separate DB_* fields (no URL-encoding needed for special characters in the password)."""
    import psycopg

    if os.environ.get("DATABASE_URL"):
        return psycopg.connect(os.environ["DATABASE_URL"])
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
    )


def _text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    return text or None


def build_address(street: Any, postcode: Any, city: Any) -> str | None:
    city_part = " ".join(p for p in (_text(postcode), _text(city)) if p)
    return ", ".join(p for p in (_text(street), city_part) if p) or None


def split_address(adres: str | None) -> tuple[str, str, str]:
    """'ul. X 1, 00-001 Miasto' -> (street, postcode, city)."""
    street, _, rest = (adres or "").partition(",")
    match = re.match(r"\s*(\d{2}-\d{3})?\s*(.*)$", rest)
    return street.strip(), match.group(1) or "", match.group(2).strip()


def _slug(text: str) -> str:
    """'Urząd Miasta Łódź' -> 'urzad-miasta-lodz' (do kluczy rekordów)."""
    text = unicodedata.normalize("NFKD", text.casefold().replace("ł", "l")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "x"


def _operator_id(cursor, name: str | None, cache: dict[str, int]) -> int | None:
    if not name:
        return None
    key = name.strip().casefold()
    if key in cache:
        return cache[key]
    # operatorzy_nazwa_uidx is an expression index, so ON CONFLICT can't target it by column
    row = cursor.execute(
        "SELECT operator_id FROM operatorzy WHERE lower(btrim(nazwa)) = lower(btrim(%s))", (name,)
    ).fetchone()
    if row is None:
        row = cursor.execute("INSERT INTO operatorzy (nazwa) VALUES (btrim(%s)) RETURNING operator_id", (name,)).fetchone()
    cache[key] = row[0]
    return row[0]


def record_key(dysponent: str | None, external_id: str | None) -> str | None:
    """Klucz rekordu w źródle: ID z pliku jest unikalne tylko u danego dysponenta, więc dostaje jego prefiks.

    Bez tego dwie instytucje używające ID "1", "2", ... nadpisywałyby sobie nawzajem rekordy.
    """
    if not external_id:
        return None
    return f"{_slug(dysponent or '')}:{external_id}"


def save_records(records: Iterable[dict[str, Any]]) -> int:
    """Upsert validated web-schema records in one transaction; returns how many were written.

    A record that matches an existing web submission by (nazwa, lat, lon) updates that row,
    mirroring the duplicate rule the CSV merge used.
    """
    records = list(records)
    if not records:
        return 0

    with _connect() as conn, conn.cursor() as cur:
        existing = {
            (nazwa, lat, lon): zrodlo_id
            for zrodlo_id, nazwa, lat, lon in cur.execute(
                "SELECT zrodlo_id, nazwa, lat, lon FROM defibrylatory WHERE zrodlo = %s", (SOURCE,)
            )
        }
        operators: dict[str, int] = {}
        params = []
        for rec in records:
            lat, lon = float(rec["szerokosc_geo"]), float(rec["dlugosc_geo"])
            nazwa = _text(rec.get("nazwa_obiektu"))
            dysponent = _text(rec.get("dysponent"))

            zrodlo_id = existing.get((nazwa, lat, lon)) or record_key(dysponent, _text(rec.get("id_zewnetrzne")))
            if not zrodlo_id:
                zrodlo_id = f"ZGL-{uuid.uuid4().hex[:6].upper()}"
            existing[(nazwa, lat, lon)] = zrodlo_id

            availability = (_text(rec.get("dostepnosc")) or "").lower()
            hours = _text(rec.get("godziny_otwarcia"))
            if availability == "24/7" and not hours:
                hours = "24/7"

            params.append(
                {
                    "zrodlo": SOURCE,
                    "zrodlo_id": zrodlo_id,
                    "lat": lat,
                    "lon": lon,
                    "nazwa": nazwa,
                    "gdzie_dokladnie": _text(rec.get("opis_lokalizacji")),
                    "adres": build_address(rec.get("ulica_i_numer"), rec.get("kod_pocztowy"), rec.get("miasto")),
                    "dostepnosc": AVAILABILITY_MAP.get(availability, "nieznana"),
                    "godziny_otwarcia": hours,
                    "operator_id": _operator_id(cur, dysponent, operators),
                    "telefon": _text(rec.get("kontakt_telefon")),
                    "status": _text(rec.get("status")),
                }
            )
        # two rows of one upload resolving to the same key would hit ON CONFLICT twice in one statement; last wins
        cur.executemany(UPSERT_SQL, list({p["zrodlo_id"]: p for p in params}.values()))
    _invalidate_cache()
    return len(params)


# --- cached reads: the list and the MCOD feed are requested often and the table can hold tens of thousands of rows ---
CACHE_SECONDS = float(os.environ.get("AED_CACHE_SECONDS", "30"))
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "items": None, "fingerprint": None}


def _invalidate_cache() -> None:
    with _cache_lock:
        _cache.update(at=0.0, items=None, fingerprint=None)


def _fingerprint(items: list[dict[str, Any]]) -> str:
    ordered = sorted(items, key=lambda i: str(i["id_zewnetrzne"]))
    return hashlib.md5(json.dumps(ordered, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]


def load_items(use_cache: bool = True) -> list[dict[str, Any]]:
    """All stored AEDs in the web-schema shape; treat the returned list as read-only (it may be shared)."""
    with _cache_lock:
        if use_cache and _cache["items"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["items"]
    items = _load_items_uncached()
    with _cache_lock:
        _cache.update(at=time.monotonic(), items=items, fingerprint=_fingerprint(items))
    return items


def data_fingerprint() -> str:
    """Short hash of the whole dataset; changes whenever any AED is added or edited."""
    load_items()
    with _cache_lock:
        return _cache["fingerprint"]


def _load_items_uncached() -> list[dict[str, Any]]:
    """All stored AEDs in the web-schema shape used by /api/aed-list and the CSV export."""
    items = []
    with _connect() as conn:
        for zrodlo, zrodlo_id, nazwa, adres, gdzie, lat, lon, dost, godz, status, oper, tel in conn.execute(LIST_SQL):
            street, postcode, city = split_address(adres)
            items.append(
                {
                    "id_zewnetrzne": zrodlo_id if zrodlo == SOURCE else f"{zrodlo}:{zrodlo_id}",
                    "nazwa_obiektu": nazwa or "",
                    "miasto": city,
                    "kod_pocztowy": postcode,
                    "ulica_i_numer": street,
                    "opis_lokalizacji": gdzie or "",
                    "szerokosc_geo": lat,
                    "dlugosc_geo": lon,
                    "dostepnosc": dost or "",
                    "godziny_otwarcia": godz or "",
                    "status": status or "",
                    "dysponent": oper or "",
                    "kontakt_telefon": tel or "",
                }
            )
    return items
