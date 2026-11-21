from typing import Any, Dict
from fastapi import FastAPI, UploadFile, File, Form, Body, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pathlib import Path
import shutil
import os
import re
import uuid
import pandas as pd
from validator import process_and_validate_excel

app = FastAPI(title="Polska Baza AED - Ogólnopolski system zgłaszania i aktualizacji lokalizacji defibrylatorów AED.")

BASE_DIR = Path(__file__).resolve().parent
MASTER_CSV_PATH = BASE_DIR / "master_aed_dane_gov.csv"
TEMP_DIR = BASE_DIR / "temp_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)


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


@app.get("/icon.png")
async def serve_logo():
    logo_path = BASE_DIR / "icon.png"
    if not logo_path.exists():
        # Zapasowe sprawdzenie w folderze ../assets, jeśli pliku nie ma bezpośrednio obok main.py
        assets_path = BASE_DIR.parent / "assets" / "icon.png"
        if assets_path.exists():
            return FileResponse(assets_path, media_type="image/png")
        raise HTTPException(status_code=404, detail="Brak pliku icon.png")
    return FileResponse(logo_path, media_type="image/png")


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

        result = process_and_validate_excel(str(temp_path))
    finally:
        if temp_path.exists():
            os.remove(temp_path)

    if "error" in result:
        return {
            "status": "rejected",
            "reason": result["error"],
            "details": result.get("missing_columns", []),
        }

    if result["total_rows"] == 0:
        return {
            "status": "rejected",
            "reason": "Przesłany arkusz Excel nie zawiera żadnych wierszy z danymi.",
            "details": [],
        }

    if not result["success"]:
        return {
            "status": "validation_errors",
            "message": f"Znaleziono błędy w {len(result['errors'])} polach (na {result['total_rows']} wierszy). Popraw plik i prześlij ponownie.",
            "total_rows": result["total_rows"],
            "errors": result["errors"],
        }

    merge_into_master(result["clean_dataframe"])

    return {
        "status": "accepted",
        "message": "Dziękujemy za wysłanie bazy danych!\nTwój wkład może komuś ocalić życie.",
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
    godziny_otwarcia = str(payload.get("godziny_otwarcia", "")).strip()
    kontakt_telefon = str(payload.get("kontakt_telefon", "")).strip()

    if not nazwa_obiektu:
        errors.append({"pole": "nazwa_obiektu", "komunikat": "Pole Nazwa obiektu jest obowiązkowe."})
    if not miasto:
        errors.append({"pole": "miasto", "komunikat": "Pole Miasto jest obowiązkowe."})
    if not adres:
        errors.append({"pole": "adres", "komunikat": "Pole Adres jest obowiązkowe."})
    if not opis_lokalizacji:
        errors.append({"pole": "opis_lokalizacji", "komunikat": "Pole Opis lokalizacji jest obowiązkowe."})
    if status not in {"sprawny", "w_serwisie", "nieaktywny"}:
        errors.append({"pole": "status", "komunikat": "Wybierz poprawny status (sprawny, w_serwisie lub nieaktywny)."})

    if dostepnosc and dostepnosc not in {"24/7", "godziny_otwarcia", "ograniczona"}:
        errors.append({"pole": "dostepnosc", "komunikat": "Nieprawidłowa wartość dostępności."})

    if godziny_otwarcia:
        if not re.search(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", godziny_otwarcia):
            errors.append({
                "pole": "godziny_otwarcia",
                "komunikat": "Podaj godziny w formacie przedziału czasowego, np. Pn-Pt 08:00-16:00."
            })

    if kontakt_telefon:
        digits = re.sub(r"[\s\-\(\)]", "", kontakt_telefon)
        if digits.startswith("+48"):
            digits = digits[3:]
        elif digits.startswith("0048"):
            digits = digits[4:]
        if not re.fullmatch(r"[1-8]\d{8}", digits):
            errors.append({
                "pole": "kontakt_telefon",
                "komunikat": "Podaj poprawny 9-cyfrowy polski numer telefonu (np. +48 22 443 62 00 lub 500 600 700)."
            })

    if errors:
        return {
            "status": "validation_errors",
            "message": f"Znaleziono błędy w {len(errors)} polach. Popraw formularz i spróbuj ponownie.",
            "errors": errors,
        }

    record = {
        "id_zewnetrzne": f"ZGL-{uuid.uuid4().hex[:6].upper()}",
        "nazwa_obiektu": nazwa_obiektu,
        "miasto": miasto,
        "kod_pocztowy": "",
        "ulica_i_numer": adres,
        "opis_lokalizacji": opis_lokalizacji,
        "szerokosc_geo": None,
        "dlugosc_geo": None,
        "dostepnosc": dostepnosc if dostepnosc else "nieokreslona",
        "godziny_otwarcia": godziny_otwarcia if dostepnosc != "24/7" else "",
        "status": status,
        "dysponent": "Zgłoszenie indywidualne",
        "kontakt_telefon": kontakt_telefon,
    }

    merge_into_master(pd.DataFrame([record]))

    return {
        "status": "accepted",
        "message": "Dziękujemy za dodanie defibrylatora!\nTwój wkład może komuś ocalić życie.",
    }


@app.get("/dane-gov-export.csv")
async def get_machine_readable_dataset():
    if not MASTER_CSV_PATH.exists():
        raise HTTPException(status_code=404, detail="Zbiór danych jest jeszcze pusty. Prześlij najpierw plik Excel.")
    return FileResponse(
        MASTER_CSV_PATH,
        media_type="text/csv; charset=utf-8",
        filename="aed_polska_dane_gov.csv",
    )