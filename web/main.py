from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pathlib import Path
import shutil
import os
import uuid
import pandas as pd
from validator import process_and_validate_excel

app = FastAPI(title="Misja AED - Walidator i Agregator Danych")

BASE_DIR = Path(__file__).resolve().parent
MASTER_CSV_PATH = BASE_DIR / "master_aed_dane_gov.csv"
TEMP_DIR = BASE_DIR / "temp_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)


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

    new_df: pd.DataFrame = result["clean_dataframe"]

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

    return {
        "status": "accepted",
        "message": "Dziękujemy za wysłanie bazy danych!\nTwój wkład może komuś ocalić życie.",
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