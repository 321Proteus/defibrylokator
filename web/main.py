from typing import Any, Dict
from fastapi import FastAPI, Request, UploadFile, File, Form, Body, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pathlib import Path
import shutil
import hashlib
import json
import os
import re
import uuid
import pandas as pd
import aed_db
import feed
from fastapi.concurrency import run_in_threadpool
from validator import (
    ADDRESS_NOT_FOUND_MSG,
    POSTCODE_RE,
    find_address_details,
    find_coordinates,
    find_postcode,
    process_and_validate_excel,
)

app = FastAPI(title="Polska Baza AED - Ogólnopolski system zgłaszania i aktualizacji lokalizacji defibrylatorów AED.")

BASE_DIR = Path(__file__).resolve().parent
MASTER_CSV_PATH = BASE_DIR / "master_aed_dane_gov.csv"
TEMP_DIR = BASE_DIR / "temp_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)

ALLOWED_STATUSES = {"sprawny", "w_serwisie", "nieaktywny"}
ALLOWED_AVAILABILITIES = {"24/7", "godziny_otwarcia", "ograniczona"}
PHONE_CLEAN_RE = re.compile(r"[\s\-\(\)]")
PL_PHONE_RE = re.compile(r"[1-8]\d{8}")
CITY_RE = re.compile(r"^[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż\s\-]{2,60}$")
LAT_RANGE = (49.0, 54.9)
LON_RANGE = (14.1, 24.2)
HAS_LETTER_RE = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]")


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


@app.get("/api/aed-list")
async def get_aed_list():
    """Postgres is the source of truth when DATABASE_URL is set; otherwise the master CSV."""
    if aed_db.enabled():
        try:
            return {"items": await run_in_threadpool(aed_db.load_items)}
        except Exception as exc:
            print(f"/api/aed-list: błąd bazy danych: {exc}")
            raise HTTPException(status_code=503, detail="Baza danych jest chwilowo niedostępna.")
    return {"items": load_items_from_csv()}


@app.post("/upload-aed")
async def upload_excel(
    file: UploadFile = File(...),
    declaration: bool = Form(False)
):
    if not declaration:
        raise HTTPException(
            status_code=400,
            detail="Aby przesłać dane, należy zaakceptować oświadczenie o ich rzetelności."
        )

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
        return {
            "status": "validation_errors",
            "message": f"Znaleziono błędy w {len(result.get('errors', []))} polach (na {result['total_rows']} wierszy). Plik został odrzucony — popraw błędy i prześlij ponownie.",
            "total_rows": result["total_rows"],
            "errors": result.get("errors", []),
        }

    try:
        await run_in_threadpool(store_records, result["clean_dataframe"])
    except Exception as exc:
        print(f"/upload-aed: błąd zapisu: {exc}")
        raise HTTPException(status_code=503, detail="Nie udało się zapisać danych w bazie. Spróbuj ponownie później.")

    return {
        "status": "accepted",
        "message": f"Dziękujemy za wysłanie bazy danych! Zweryfikowano i przyjęto {result['valid_rows']} rekordów.\nTwój wkład może komuś ocalić życie.",
    }


@app.post("/add-aed")
async def add_single_aed(payload: Dict[str, Any] = Body(...)):
    if payload.get("declaration") is not True:
        raise HTTPException(
            status_code=400,
            detail="Aby przesłać dane, należy zaakceptować oświadczenie o ich rzetelności."
        )

    errors = []

    nazwa_obiektu = str(payload.get("nazwa_obiektu", "")).strip()
    miasto = str(payload.get("miasto", "")).strip()
    adres = str(payload.get("adres", "")).strip()
    opis_lokalizacji = str(payload.get("opis_lokalizacji", "")).strip()
    status = str(payload.get("status", "")).strip()
    dostepnosc = str(payload.get("dostepnosc", "")).strip()
    kontakt_telefon = str(payload.get("kontakt_telefon", "")).strip()

    if not nazwa_obiektu or len(nazwa_obiektu) < 3 or not HAS_LETTER_RE.search(nazwa_obiektu):
        errors.append({
            "pole": "nazwa_obiektu",
            "komunikat": "Wpisz poprawną nazwę obiektu (minimum 3 znaki, musi zawierać litery)."
        })

    if not miasto or not CITY_RE.fullmatch(miasto):
        errors.append({
            "pole": "miasto",
            "komunikat": "Wpisz poprawną nazwę miasta (tylko litery, spacje i myślniki, bez cyfr)."
        })

    if not adres or len(adres) < 3 or not HAS_LETTER_RE.search(adres):
        errors.append({
            "pole": "adres",
            "komunikat": "Wpisz poprawny adres (minimum 3 znaki, np. ul. Rakowiecka 25)."
        })

    if not opis_lokalizacji or len(opis_lokalizacji) < 4 or not HAS_LETTER_RE.search(opis_lokalizacji):
        errors.append({
            "pole": "opis_lokalizacji",
            "komunikat": "Wpisz dokładniejszy opis lokalizacji (minimum 4 znaki, np. Parter przy recepcji)."
        })

    if status not in ALLOWED_STATUSES:
        errors.append({
            "pole": "status",
            "komunikat": "Wybierz poprawny status (sprawny, w_serwisie lub nieaktywny)."
        })

    if dostepnosc and dostepnosc not in ALLOWED_AVAILABILITIES:
        errors.append({
            "pole": "dostepnosc",
            "komunikat": "Nieprawidłowa wartość dostępności."
        })

    if kontakt_telefon:
        digits = PHONE_CLEAN_RE.sub("", kontakt_telefon)
        if digits.startswith("+48"):
            digits = digits[3:]
        elif digits.startswith("0048"):
            digits = digits[4:]
        if not PL_PHONE_RE.fullmatch(digits):
            errors.append({
                "pole": "kontakt_telefon",
                "komunikat": "Wpisz poprawny 9-cyfrowy polski numer telefonu (np. +48 22 443 62 00 lub 500 600 700)."
            })

    lat_raw = str(payload.get("szerokosc_geo") or "").strip().replace(",", ".")
    lon_raw = str(payload.get("dlugosc_geo") or "").strip().replace(",", ".")
    kod_pocztowy = str(payload.get("kod_pocztowy") or "").strip()
    lat = lon = None

    if lat_raw or lon_raw:
        try:
            lat, lon = float(lat_raw), float(lon_raw)
        except ValueError:
            errors.append({"pole": "szerokosc_geo", "komunikat": "Wpisz współrzędne jako liczby, np. 52.2297 i 21.0122."})
        else:
            if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
                errors.append({
                    "pole": "szerokosc_geo",
                    "komunikat": f"Współrzędne poza granicami Polski (szerokość {LAT_RANGE[0]}–{LAT_RANGE[1]}, długość {LON_RANGE[0]}–{LON_RANGE[1]}).",
                })

    if kod_pocztowy and not POSTCODE_RE.fullmatch(kod_pocztowy):
        errors.append({"pole": "kod_pocztowy", "komunikat": "Kod pocztowy musi mieć format 00-000 (np. 02-517)."})

    if errors:
        return {
            "status": "validation_errors",
            "message": f"Znaleziono błędy w {len(errors)} polach. Popraw formularz i spróbuj ponownie.",
            "errors": errors,
        }

    if lat is None:
        coords = await run_in_threadpool(find_coordinates, adres, miasto)
        if coords is None:
            return {
                "status": "validation_errors",
                "message": "Nie znaleziono tego adresu. Popraw formularz i spróbuj ponownie.",
                "errors": [{"pole": "adres", "komunikat": ADDRESS_NOT_FOUND_MSG}],
            }
        lat, lon = coords
    lat, lon = round(lat, 6), round(lon, 6)
    if not kod_pocztowy:
        kod_pocztowy = await run_in_threadpool(find_postcode, lat, lon) or ""

    record = {
        "id_zewnetrzne": f"ZGL-{uuid.uuid4().hex[:6].upper()}",
        "nazwa_obiektu": nazwa_obiektu,
        "miasto": miasto,
        "kod_pocztowy": kod_pocztowy,
        "ulica_i_numer": adres,
        "opis_lokalizacji": opis_lokalizacji,
        "szerokosc_geo": lat,
        "dlugosc_geo": lon,
        "dostepnosc": dostepnosc or "nieokreslona",
        "godziny_otwarcia": "",
        "status": status,
        "dysponent": "Zgłoszenie indywidualne",
        "kontakt_telefon": kontakt_telefon,
    }

    try:
        await run_in_threadpool(store_records, pd.DataFrame([record]))
    except Exception as exc:
        print(f"/add-aed: błąd zapisu: {exc}")
        raise HTTPException(status_code=503, detail="Nie udało się zapisać danych w bazie. Spróbuj ponownie później.")

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
    """Feed XML; the resource URL points back at this app (PUBLIC_BASE_URL overrides the request's host)."""
    base = os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)
    if aed_db.enabled():
        items = await run_in_threadpool(aed_db.load_items)
    else:
        items = load_items_from_csv()
    fingerprint = hashlib.md5(
        json.dumps(sorted(items, key=lambda i: str(i["id_zewnetrzne"])), sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return feed.build_feed(f"{base.rstrip('/')}/dane-gov-export.csv?v={fingerprint}", len(items))


@app.api_route("/aed-feed.xml", methods=["GET", "HEAD"])
async def aed_feed_xml(request: Request):
    return Response(content=await _feed_content(request), media_type="application/xml; charset=utf-8")


@app.api_route("/aed-feed.md5", methods=["GET", "HEAD"])
async def aed_feed_md5(request: Request):
    return Response(content=feed.feed_md5(await _feed_content(request)) + "\n", media_type="text/plain")