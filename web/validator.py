import pandas as pd
from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Any, Optional
import re
import math


class AEDRecord(BaseModel):
    id_zewnetrzne: Optional[str] = Field(None, description="ID w bazie dysponenta")
    nazwa_obiektu: str = Field(..., min_length=2, description="Np. Urząd Gminy, Dworzec PKP")
    miasto: str = Field(..., min_length=2)
    kod_pocztowy: str = Field(..., pattern=r"^\d{2}-\d{3}$")
    ulica_i_numer: str = Field(..., min_length=2)
    opis_lokalizacji: str = Field(..., min_length=3, description="Np. Parter, przy recepcji na ścianie")
    szerokosc_geo: float = Field(..., ge=49.0, le=54.9, description="Latitude (granice Polski)")
    dlugosc_geo: float = Field(..., ge=14.1, le=24.2, description="Longitude (granice Polski)")
    dostepnosc: str = Field(..., description="24/7, godziny_otwarcia, ograniczona")
    godziny_otwarcia: Optional[str] = Field(None, description="Wymagane, jeśli dostępność != 24/7")
    status: str = Field(..., description="sprawny, w_serwisie, nieaktywny")
    dysponent: str = Field(..., min_length=2, description="Nazwa instytucji odpowiedzialnej")
    kontakt_telefon: Optional[str] = None

    @field_validator("dostepnosc")
    @classmethod
    def validate_dostepnosc(cls, v: str) -> str:
        allowed = {"24/7", "godziny_otwarcia", "ograniczona"}
        v_clean = v.strip().lower()
        if v_clean not in allowed:
            raise ValueError(f"Niedozwolona wartość '{v}'. Dozwolone opcje: 24/7, godziny_otwarcia, ograniczona.")
        return v_clean

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"sprawny", "w_serwisie", "nieaktywny"}
        v_clean = v.strip().lower()
        if v_clean not in allowed:
            raise ValueError(f"Niedozwolony status '{v}'. Dozwolone opcje: sprawny, w_serwisie, nieaktywny.")
        return v_clean

    @field_validator("kontakt_telefon")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        digits = re.sub(r"[\s\-\+]", "", str(v))
        if not digits.isdigit() or len(digits) < 9:
            raise ValueError("Niepoprawny numer telefonu (wymagane min. 9 cyfr).")
        return v.strip()


REQUIRED_COLUMNS = {
    "nazwa_obiektu",
    "miasto",
    "kod_pocztowy",
    "ulica_i_numer",
    "opis_lokalizacji",
    "szerokosc_geo",
    "dlugosc_geo",
    "dostepnosc",
    "status",
    "dysponent",
}


def translate_pydantic_error(err: Dict[str, Any]) -> str:
    """Tłumaczy domyślne błędy walidacji Pydantic na czytelny język polski."""
    err_type = err.get("type", "")
    msg = err.get("msg", "")
    ctx = err.get("ctx", {})

    # Własne błędy z naszych @field_validator (zaczynają się od "Value error, ")
    if err_type == "value_error":
        return msg.replace("Value error, ", "")

    if err_type == "string_type":
        return "Pole jest puste lub zawiera niepoprawny format (wymagany tekst)."
    if err_type == "float_parsing" or err_type == "float_type":
        return "Wymagana poprawna liczba (np. 52.2297)."
    if err_type == "string_too_short":
        min_len = ctx.get("min_length", 2)
        return f"Tekst jest za krótki (wymagane minimum {min_len} znaki)."
    if err_type == "string_pattern_mismatch":
        return "Niepoprawny format danych (np. kod pocztowy musi mieć format 00-000)."
    if err_type == "greater_than_equal":
        ge_val = ctx.get("ge")
        return f"Wartość musi być większa lub równa {ge_val} (poza granicami Polski)."
    if err_type == "less_than_equal":
        le_val = ctx.get("le")
        return f"Wartość musi być mniejsza lub równa {le_val} (poza granicami Polski)."
    if err_type == "missing":
        return "Brak wymaganej wartości w komórce."

    return msg


def validate_single_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Waliduje pojedynczy rekord AED (z formularza). Zwraca {'success', 'errors', 'record'}."""
    data = {
        k: (v.strip() if isinstance(v, str) else v)
        for k, v in raw.items()
        if k in AEDRecord.model_fields
    }
    data = {k: (None if v == "" else v) for k, v in data.items()}

    errors: List[Dict[str, str]] = []

    if str(data.get("dostepnosc")).lower() != "24/7" and not data.get("godziny_otwarcia"):
        errors.append({
            "pole": "godziny_otwarcia",
            "komunikat": "Dla dostępności innej niż '24/7' należy podać godziny otwarcia.",
        })

    record = None
    try:
        record = AEDRecord(**data)
    except Exception as e:
        for err in e.errors():
            errors.append({
                "pole": ".".join(str(loc) for loc in err["loc"]),
                "komunikat": translate_pydantic_error(err),
            })

    return {
        "success": not errors,
        "errors": errors,
        "record": record.model_dump() if record and not errors else None,
    }


def process_and_validate_excel(file_path: str) -> Dict[str, Any]:
    try:
        df = pd.read_excel(file_path, dtype={"kod_pocztowy": str, "kontakt_telefon": str})
        df = df.where(pd.notnull(df), None)
    except Exception as e:
        return {"success": False, "error": f"Nie udało się odczytać pliku Excel: {str(e)}"}

    df.columns = [str(col).strip().lower() for col in df.columns]

    missing_cols = REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        return {
            "success": False,
            "error": "Brak wymaganych kolumn w pliku Excel.",
            "missing_columns": list(missing_cols),
        }

    valid_records: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for idx, row in df.iterrows():
        excel_row_num = idx + 2
        row_dict = {
            k: (None if isinstance(v, float) and math.isnan(v) else v)
            for k, v in row.to_dict().items()
        }

        if str(row_dict.get("dostepnosc")).strip().lower() != "24/7" and not row_dict.get("godziny_otwarcia"):
            errors.append({
                "wiersz": excel_row_num,
                "pole": "godziny_otwarcia",
                "komunikat": "Dla dostępności innej niż '24/7' należy podać godziny otwarcia."
            })
            continue

        try:
            record = AEDRecord(**row_dict)
            valid_records.append(record.model_dump())
        except Exception as e:
            for err in e.errors():
                errors.append({
                    "wiersz": excel_row_num,
                    "pole": ".".join(str(loc) for loc in err["loc"]),
                    "komunikat": translate_pydantic_error(err),
                })

    clean_df = pd.DataFrame(valid_records)
    duplicates_removed = 0
    if not clean_df.empty:
        before = len(clean_df)
        clean_df = clean_df.drop_duplicates(subset=["szerokosc_geo", "dlugosc_geo", "nazwa_obiektu"])
        duplicates_removed = before - len(clean_df)

    return {
        "success": len(errors) == 0,
        "total_rows": len(df),
        "valid_rows": len(clean_df),
        "duplicates_removed": duplicates_removed,
        "errors": errors,
        "clean_dataframe": clean_df,
    }