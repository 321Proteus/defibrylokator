import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict

import pandas as pd
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

import aed_db
import feed
import nearby
from pydantic import ValidationError

from validator import (
    ADDRESS_NOT_FOUND_MSG,
    INDIVIDUAL_SUBMITTER,
    LAT_RANGE,
    LON_RANGE,
    AEDRecord,
    errors_from,
    find_address_details,
    find_coordinates,
    pl_plural,
    process_and_validate_excel,
    validate_single_submission,
)

app = FastAPI(title="Polska Baza AED - Ogólnopolski system zgłaszania i aktualizacji lokalizacji defibrylatorów AED.")
# /api/aed-list i eksport CSV mają kilka MB przy dziesiątkach tysięcy rekordów; JSON kompresuje się ~10x.
app.add_middleware(GZipMiddleware, minimum_size=1024)

BASE_DIR = Path(__file__).resolve().parent
MASTER_CSV_PATH = BASE_DIR / "master_aed_dane_gov.csv"  # tylko gdy brak bazy (tryb lokalny bez DATABASE_URL / DB_*)
TEMP_DIR = BASE_DIR / "temp_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)

SAVE_FAILED_MSG = "Nie udało się zapisać danych w bazie. Spróbuj ponownie później."
DECLARATION_MSG = "Aby przesłać dane, należy zaakceptować oświadczenie o ich rzetelności."


def merge_into_master(new_df: pd.DataFrame) -> None:
    if MASTER_CSV_PATH.exists():
        existing_df = pd.read_csv(
            MASTER_CSV_PATH,
            sep=";",
            encoding="utf-8-sig",
            dtype={"kod_pocztowy": str, "kontakt_telefon": str}
        )
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        combined_df = combined_df.drop_duplicates(
            subset=["szerokosc_geo", "dlugosc_geo", "nazwa_obiektu"],
            keep="last"
        )
    else:
        combined_df = new_df

    combined_df.to_csv(MASTER_CSV_PATH, sep=";", index=False, encoding="utf-8-sig")


def store_records(new_df: pd.DataFrame) -> None:
    """Persist validated submissions: Postgres when configured, else the master CSV."""
    if aed_db.enabled():
        aed_db.save_records(new_df.to_dict("records"))
    else:
        merge_into_master(new_df)


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html_path = BASE_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="Brak pliku index.html w katalogu głównym.")
    return html_path.read_text(encoding="utf-8")


@app.get("/style.css")
async def serve_css():
    css_path = BASE_DIR / "style.css"
    if not css_path.exists():
        raise HTTPException(status_code=404, detail="Brak pliku style.css")
    return FileResponse(css_path, media_type="text/css")


@app.get("/request.js")
async def serve_js():
    js_path = BASE_DIR / "request.js"
    if not js_path.exists():
        raise HTTPException(status_code=404, detail="Brak pliku request.js")
    return FileResponse(js_path, media_type="application/javascript")


@app.get("/favicon.svg")
async def serve_favicon():
    icon_path = BASE_DIR.parent / "assets" / "icon.svg"
    if not icon_path.exists():
        raise HTTPException(status_code=404, detail="Brak pliku assets/icon.svg")
    return FileResponse(icon_path, media_type="image/svg+xml")


@app.get("/favicon.ico", include_in_schema=False)
async def serve_favicon_ico():
    return RedirectResponse("/favicon.svg", status_code=301)


@app.get("/icon.png")
async def serve_logo():
    logo_path = BASE_DIR / "icon.png"
    if logo_path.exists():
        return FileResponse(logo_path, media_type="image/png")
    assets_path = BASE_DIR.parent / "assets" / "icon.png"
    if assets_path.exists():
        return FileResponse(assets_path, media_type="image/png")
    raise HTTPException(status_code=404, detail="Brak pliku icon.png")


def load_items_from_csv():
    if not MASTER_CSV_PATH.exists():
        return []

    df = pd.read_csv(
        MASTER_CSV_PATH,
        sep=";",
        encoding="utf-8-sig",
        dtype=str
    ).fillna("")

    items = []
    for _, row in df.iterrows():
        lat_raw = str(row.get("szerokosc_geo", "")).strip().replace(",", ".")
        lon_raw = str(row.get("dlugosc_geo", "")).strip().replace(",", ".")
        try:
            lat = float(lat_raw)
            lon = float(lon_raw)
        except ValueError:
            continue

        if pd.isna(lat) or pd.isna(lon):
            continue

        items.append({
            "id_zewnetrzne": row.get("id_zewnetrzne", ""),
            "nazwa_obiektu": row.get("nazwa_obiektu", ""),
            "miasto": row.get("miasto", ""),
            "kod_pocztowy": row.get("kod_pocztowy", ""),
            "ulica_i_numer": row.get("ulica_i_numer", ""),
            "opis_lokalizacji": row.get("opis_lokalizacji", ""),
            "szerokosc_geo": lat,
            "dlugosc_geo": lon,
            "dostepnosc": row.get("dostepnosc", ""),
            "godziny_otwarcia": row.get("godziny_otwarcia", ""),
            "status": row.get("status", ""),
            "dysponent": row.get("dysponent", ""),
            "kontakt_telefon": row.get("kontakt_telefon", ""),
        })
    return items


async def _all_items() -> list:
    """Every stored AED: Postgres when configured (cached in aed_db), otherwise the master CSV."""
    if aed_db.enabled():
        try:
            return await run_in_threadpool(aed_db.load_items)
        except Exception as exc:
            print(f"błąd bazy danych: {exc}")
            raise HTTPException(status_code=503, detail="Baza danych jest chwilowo niedostępna.")
    return await run_in_threadpool(load_items_from_csv)


@app.get("/api/aed-nearby")
async def get_aed_nearby(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    limit: int = Query(nearby.DEFAULT_LIMIT, ge=1, le=nearby.MAX_LIMIT),
):
    """The `limit` AEDs closest to a point (user location or map centre); what the map loads instead of everything."""
    result = nearby.nearest(await _all_items(), lat, lon, limit)
    return JSONResponse(result, headers={"Cache-Control": "public, max-age=30"})


@app.get("/api/aed-list")
async def get_aed_list():
    """Every AED at once (large: tens of thousands of rows). The map uses /api/aed-nearby instead."""
    return {"items": await _all_items()}


@app.post("/upload-aed")
async def upload_excel(
    file: UploadFile = File(...),
    declaration: bool = Form(False)
):
    if not declaration:
        raise HTTPException(status_code=400, detail=DECLARATION_MSG)

    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Dozwolone są wyłącznie pliki Excel (.xlsx, .xls).")

    ext = os.path.splitext(file.filename)[1]
    temp_path = TEMP_DIR / f"{uuid.uuid4()}{ext}"

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        result = await run_in_threadpool(process_and_validate_excel, str(temp_path))
    finally:
        if temp_path.exists():
            os.remove(temp_path)

    if "error" in result:
        return {
            "status": "rejected",
            "reason": result["error"],
            "details": result.get("missing_columns", []),
            "extra_columns": result.get("extra_columns", []),
        }

    if result.get("total_rows", 0) == 0:
        return {
            "status": "rejected",
            "reason": "Przesłany arkusz Excel nie zawiera żadnych wierszy z danymi.",
            "details": [],
        }

    if not result.get("success") or result.get("valid_rows", 0) == 0:
        n_errors = len(result.get("errors", []))
        return {
            "status": "validation_errors",
            "message": (
                f"Znaleziono {n_errors} {pl_plural(n_errors, 'błąd', 'błędy', 'błędów')} w pliku "
                f"({result['total_rows']} {pl_plural(result['total_rows'], 'wiersz', 'wiersze', 'wierszy')} danych). "
                "Plik został odrzucony — popraw błędy i prześlij ponownie."
            ),
            "total_rows": result["total_rows"],
            "errors": result.get("errors", []),
        }

    try:
        await run_in_threadpool(store_records, result["clean_dataframe"])
    except Exception as exc:
        print(f"/upload-aed: błąd zapisu: {exc}")
        raise HTTPException(status_code=503, detail=SAVE_FAILED_MSG)

    n = result["valid_rows"]
    return {
        "status": "accepted",
        "message": (
            f"Dziękujemy za wysłanie bazy danych! Zweryfikowano i przyjęto {n} {pl_plural(n, 'rekord', 'rekordy', 'rekordów')}."
            "\nTwój wkład może komuś ocalić życie."
        ),
    }


def _form_errors_response(errors: list) -> dict:
    n = len(errors)
    return {
        "status": "validation_errors",
        "message": f"Znaleziono {n} {pl_plural(n, 'błąd', 'błędy', 'błędów')} w formularzu. Popraw go i spróbuj ponownie.",
        "errors": errors,
    }


@app.post("/add-aed")
async def add_single_aed(payload: Dict[str, Any] = Body(...)):
    if payload.get("declaration") is not True:
        raise HTTPException(status_code=400, detail=DECLARATION_MSG)

    # Reguły pól są w validator.py (te same co dla pliku Excel); tu zostaje tylko uzupełnianie danych.
    draft, errors = validate_single_submission(payload)
    if errors:
        return _form_errors_response(errors)

    # Współrzędne i kod pocztowy mogą pochodzić z podglądu na mapie (użytkownik mógł je poprawić);
    # brakujące uzupełniamy z adresu (geokodowanie) i ze współrzędnych (geokodowanie odwrotne).
    lat, lon, kod_pocztowy = draft.szerokosc_geo, draft.dlugosc_geo, draft.kod_pocztowy
    if lat is None:
        coords = await run_in_threadpool(find_coordinates, draft.ulica_i_numer, draft.miasto)
        if coords is None:
            return {
                "status": "validation_errors",
                "message": "Nie znaleziono tego adresu. Popraw formularz i spróbuj ponownie.",
                "errors": [{"pole": "adres", "komunikat": ADDRESS_NOT_FOUND_MSG}],
            }
        lat, lon = coords
    lat, lon = round(lat, 6), round(lon, 6)
    if not kod_pocztowy:
        _, kod_pocztowy = await run_in_threadpool(find_address_details, lat, lon)

    record = {
        "id_zewnetrzne": f"ZGL-{uuid.uuid4().hex[:6].upper()}",
        "nazwa_obiektu": draft.nazwa_obiektu,
        "miasto": draft.miasto,
        "kod_pocztowy": kod_pocztowy or "",
        "ulica_i_numer": draft.ulica_i_numer,
        "opis_lokalizacji": draft.opis_lokalizacji,
        "szerokosc_geo": lat,
        "dlugosc_geo": lon,
        "dostepnosc": draft.dostepnosc or "nieokreslona",
        "godziny_otwarcia": "",
        "status": draft.status,
        "dysponent": INDIVIDUAL_SUBMITTER,
        "kontakt_telefon": draft.kontakt_telefon,
    }

    # Pełny rekord musi spełniać te same reguły co wiersz z Excela (np. kod pocztowy ustalony z geokodowania).
    # "nieokreslona" to wartość tylko formularza, więc sprawdzamy rekord z dostępnością zastępczą.
    try:
        AEDRecord(**{**record, "dostepnosc": draft.dostepnosc or "24/7"})
    except ValidationError as exc:
        return _form_errors_response([
            {**e, "pole": "adres" if e["pole"] == "ulica_i_numer" else e["pole"]} for e in errors_from(exc)
        ])

    try:
        await run_in_threadpool(store_records, pd.DataFrame([record]))
    except Exception as exc:
        print(f"/add-aed: błąd zapisu: {exc}")
        raise HTTPException(status_code=503, detail=SAVE_FAILED_MSG)

    return {
        "status": "accepted",
        "message": "Dziękujemy za dodanie defibrylatora!\nTwój wkład może komuś ocalić życie.",
    }


@app.get("/geocode-preview")
async def geocode_preview(adres: str = "", miasto: str = ""):
    """Podgląd dla formularza: współrzędne, kod pocztowy i adres znaleziony dla wpisanego adresu."""
    adres, miasto = adres.strip(), miasto.strip()
    coords = await run_in_threadpool(find_coordinates, adres, miasto) if adres and miasto else None
    if coords is None:
        return {"found": False, "message": ADDRESS_NOT_FOUND_MSG}
    lat, lon = round(coords[0], 6), round(coords[1], 6)
    found_address, kod = await run_in_threadpool(find_address_details, lat, lon)
    return {"found": True, "lat": lat, "lon": lon, "kod_pocztowy": kod or "", "adres_znaleziony": found_address or ""}


@app.get("/reverse-preview")
async def reverse_preview(lat: float, lon: float):
    """Adres i kod pocztowy dla punktu przesuniętego ręcznie na mapie."""
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        return {"found": False, "message": "Punkt leży poza granicami Polski."}
    found_address, kod = await run_in_threadpool(find_address_details, lat, lon)
    return {"found": bool(found_address), "kod_pocztowy": kod or "", "adres_znaleziony": found_address or ""}


@app.get("/dane-gov-export.csv")
async def get_machine_readable_dataset():
    if aed_db.enabled():
        items = await run_in_threadpool(aed_db.load_items)
        if not items:
            raise HTTPException(status_code=404, detail="Zbiór danych jest jeszcze pusty.")
        csv_text = pd.DataFrame(items).to_csv(sep=";", index=False)
        return Response(
            content="\ufeff" + csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="aed_polska_dane_gov.csv"'},
        )
    if not MASTER_CSV_PATH.exists():
        raise HTTPException(status_code=404, detail="Zbiór danych jest jeszcze pusty. Prześlij najpierw plik Excel.")
    return FileResponse(
        MASTER_CSV_PATH,
        media_type="text/csv; charset=utf-8",
        filename="aed_polska_dane_gov.csv",
    )


async def _feed_content(request: Request) -> bytes:
    """Feed XML; the resource URL points back at this app (PUBLIC_BASE_URL overrides the request's host).

    MCOD re-downloads a link resource only when its metadata changes, so the CSV URL carries a
    fingerprint of the data (?v=...): new or edited AEDs -> new URL -> MCOD refreshes the file.
    """
    base = os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)
    if aed_db.enabled():
        total = len(await run_in_threadpool(aed_db.load_items))
        fingerprint = await run_in_threadpool(aed_db.data_fingerprint)
    else:
        items = load_items_from_csv()
        total = len(items)
        fingerprint = hashlib.md5(json.dumps(items, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    return feed.build_feed(f"{base.rstrip('/')}/dane-gov-export.csv?v={fingerprint}", total)


@app.api_route("/aed-feed.xml", methods=["GET", "HEAD"])
async def aed_feed_xml(request: Request):
    return Response(content=await _feed_content(request), media_type="application/xml; charset=utf-8")


@app.api_route("/aed-feed.md5", methods=["GET", "HEAD"])
async def aed_feed_md5(request: Request):
    return Response(content=feed.feed_md5(await _feed_content(request)) + "\n", media_type="text/plain")
