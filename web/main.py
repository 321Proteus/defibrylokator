from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
import shutil
import os
import uuid
import pandas as pd
from validator import process_and_validate_excel

app = FastAPI(title="Misja AED - Walidator i Agregator Danych")

MASTER_CSV_PATH = "master_aed_dane_gov.csv"
TEMP_DIR = "temp_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    if not os.path.exists("index.html"):
        raise HTTPException(status_code=500, detail="Brak pliku index.html w katalogu głównym.")
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.post("/upload-aed")
async def upload_excel(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Dozwolone są wyłącznie pliki Excel (.xlsx, .xls).")

    ext = os.path.splitext(file.filename)[1]
    temp_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}{ext}")

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        result = process_and_validate_excel(temp_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # 1. Błąd struktury pliku (brak kolumn lub uszkodzony plik)
    if "error" in result:
        return {
            "status": "rejected",
            "reason": result["error"],
            "details": result.get("missing_columns", []),
        }

    # 2. Pusty arkusz (0 wierszy z danymi)
    if result["total_rows"] == 0:
        return {
            "status": "rejected",
            "reason": "Przesłany arkusz Excel nie zawiera żadnych wierszy z danymi.",
            "details": [],
        }

    # 3. Błędy walidacji w konkretnych wierszach
    if not result["success"]:
        return {
            "status": "validation_errors",
            "message": f"Znaleziono błędy w {len(result['errors'])} polach (na {result['total_rows']} wierszy). Popraw plik i prześlij ponownie.",
            "total_rows": result["total_rows"],
            "errors": result["errors"],
        }

    new_df: pd.DataFrame = result["clean_dataframe"]

    if os.path.exists(MASTER_CSV_PATH):
        existing_df = pd.read_csv(MASTER_CSV_PATH, sep=";", dtype={"kod_pocztowy": str, "kontakt_telefon": str})
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        # keep="last" pozwala zaktualizować np. status istniejącego AED przy ponownym wgraniu
        combined_df = combined_df.drop_duplicates(
            subset=["szerokosc_geo", "dlugosc_geo", "nazwa_obiektu"],
            keep="last"
        )
    else:
        combined_df = new_df

    combined_df.to_csv(MASTER_CSV_PATH, sep=";", index=False, encoding="utf-8")

    return {
        "status": "accepted",
        "message": "Wszystkie wiersze przeszły walidację i zasiliły zbiór danych dla dane.gov.pl!",
        "added_records": result["valid_rows"],
        "duplicates_skipped": result["duplicates_removed"],
        "total_database_size": len(combined_df),
    }


@app.get("/dane-gov-export.csv")
async def get_machine_readable_dataset():
    if not os.path.exists(MASTER_CSV_PATH):
        raise HTTPException(status_code=404, detail="Zbiór danych jest jeszcze pusty. Prześlij najpierw plik Excel.")
    return FileResponse(
        MASTER_CSV_PATH,
        media_type="text/csv",
        filename="aed_polska_dane_gov.csv",
    )