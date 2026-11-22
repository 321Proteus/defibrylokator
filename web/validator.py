import os
import sys
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Any, Optional
import re
import math

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # geocode.py leży w katalogu głównym repozytorium
import geocode  # noqa: E402

MAX_GEOCODED_ROWS = int(os.environ.get("MAX_GEOCODED_ROWS", "100"))
POSTCODE_RE = re.compile(r"\b\d{2}-\d{3}\b")
CITY_RE = re.compile(r"^[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż\s\-]{2,60}$")
HAS_LETTER_RE = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]")
ADDRESS_NOT_FOUND_MSG = (
    "Nie udało się ustalić współrzędnych dla tego adresu. Sprawdź pisownię (ulica i numer, miasto) "
    "lub wpisz współrzędne ręcznie."
)

ALL_ALLOWED_COLUMNS = {
    "id_zewnetrzne",
    "nazwa_obiektu",
    "miasto",
    "kod_pocztowy",
    "ulica_i_numer",
    "opis_lokalizacji",
    "szerokosc_geo",
    "dlugosc_geo",
    "dostepnosc",
    "godziny_otwarcia",
    "status",
    "dysponent",
    "kontakt_telefon",
}

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

    @field_validator("nazwa_obiektu", "ulica_i_numer", "opis_lokalizacji", "dysponent", mode="before")
    @classmethod
    def clean_and_check_text(cls, v: Any) -> Any:
        if v is None:
            return v
        text = str(v).strip()
        if not text:
            return ""
        if not HAS_LETTER_RE.search(text):
            raise ValueError("Wartość musi zawierać litery (nie może składać się wyłącznie z cyfr lub znaków).")
        return text

    @field_validator("miasto", mode="before")
    @classmethod
    def validate_miasto(cls, v: Any) -> Any:
        if v is None:
            return v
        text = str(v).strip()
        if not CITY_RE.fullmatch(text):
            raise ValueError("Niepoprawna nazwa miasta (dozwolone tylko litery, spacje i myślniki, bez cyfr).")
        return text

    @field_validator("kod_pocztowy", mode="before")
    @classmethod
    def clean_postcode(cls, v: Any) -> Any:
        if v is None:
            return v
        return str(v).strip()

    @field_validator("szerokosc_geo", "dlugosc_geo", mode="before")
    @classmethod
    def parse_coord(cls, v: Any) -> Any:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            raise ValueError("Brak wymaganej współrzędnej geograficznej.")
        if isinstance(v, str):
            v = v.strip().replace(",", ".")
            if not v:
                raise ValueError("Brak wymaganej współrzędnej geograficznej.")
        return v

    @field_validator("dostepnosc", mode="before")
    @classmethod
    def validate_dostepnosc(cls, v: Any) -> str:
        if v is None or str(v).strip() == "":
            raise ValueError("Brak wartości dostępności (dozwolone: 24/7, godziny_otwarcia, ograniczona).")
        allowed = {"24/7", "godziny_otwarcia", "ograniczona"}
        v_clean = str(v).strip().lower()
        if v_clean not in allowed:
            raise ValueError(f"Niedozwolona wartość '{v}'. Dozwolone opcje: 24/7, godziny_otwarcia, ograniczona.")
        return v_clean

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, v: Any) -> str:
        if v is None or str(v).strip() == "":
            raise ValueError("Brak wartości statusu (dozwolone: sprawny, w_serwisie, nieaktywny).")
        allowed = {"sprawny", "w_serwisie", "nieaktywny"}
        v_clean = str(v).strip().lower()
        if v_clean not in allowed:
            raise ValueError(f"Niedozwolony status '{v}'. Dozwolone opcje: sprawny, w_serwisie, nieaktywny.")
        return v_clean

    @field_validator("kontakt_telefon", mode="before")
    @classmethod
    def validate_phone(cls, v: Optional[Any]) -> Optional[str]:
        if v is None or str(v).strip() == "":
            return None
        digits = re.sub(r"[\s\-\(\)]", "", str(v).strip())
        if digits.startswith("+48"):
            digits = digits[3:]
        elif digits.startswith("0048"):
            digits = digits[4:]
        if not re.fullmatch(r"[1-8]\d{8}", digits):
            raise ValueError("Niepoprawny polski numer telefonu (wymagane 9 cyfr, np. +48 22 443 62 00 lub 500 600 700).")
        return str(v).strip()


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or str(value).strip() == ""


def find_coordinates(ulica_i_numer: str, miasto: str) -> Optional[tuple]:
    """(lat, lon) dla adresu albo None."""
    return geocode.forward_geocode(f"{ulica_i_numer}, {miasto}")


def find_address_details(lat: float, lon: float) -> tuple:
    """(adres, kod pocztowy NN-NNN) z geokodowania odwrotnego; każdy element może być None."""
    address = geocode.reverse_geocode(lat, lon)
    match = POSTCODE_RE.search(address) if address else None
    return address, (match.group() if match else None)


def find_postcode(lat: float, lon: float) -> Optional[str]:
    """Kod pocztowy NN-NNN z geokodowania odwrotnego albo None."""
    return find_address_details(lat, lon)[1]


def translate_pydantic_error(err: Dict[str, Any]) -> str:
    """Tłumaczy domyślne błędy walidacji Pydantic na czytelny język polski."""
    err_type = err.get("type", "")
    msg = err.get("msg", "")
    ctx = err.get("ctx", {})

    if err_type == "value_error":
        return msg.replace("Value error, ", "")
    if err_type == "string_type":
        return "Pole jest puste lub zawiera niepoprawny format (wymagany tekst)."
    if err_type in ("float_parsing", "float_type"):
        return "Wymagana poprawna liczba współrzędnych WGS84 (np. 52.2297)."
    if err_type == "string_too_short":
        min_len = ctx.get("min_length", 2)
        return f"Tekst jest za krótki (wymagane minimum {min_len} znaki)."
    if err_type == "string_pattern_mismatch":
        return "Niepoprawny format danych (kod pocztowy musi mieć format 00-000)."
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
        excel_file = pd.ExcelFile(file_path)
        if not excel_file.sheet_names:
            return {"success": False, "error": "Plik Excel nie zawiera żadnych arkuszy."}
        df = excel_file.parse(sheet_name=0, dtype={"kod_pocztowy": str, "kontakt_telefon": str})
    except Exception as e:
        return {"success": False, "error": f"Nie udało się odczytać pliku Excel: {str(e)}"}

    # Usunięcie całkowicie pustych wierszy i pustych kolumn na końcu arkusza
    df = df.dropna(how="all")
    df = df.loc[:, ~df.columns.astype(str).str.match(r"^Unnamed:\s*\d+$")]
    df = df.where(pd.notnull(df), None)

    normalized_cols = [str(col).strip().lower() for col in df.columns]
    if len(normalized_cols) != len(set(normalized_cols)):
        return {
            "success": False,
            "error": "W pliku Excel znajdują się zduplikowane nazwy kolumn. Usuń powtarzające się nagłówki.",
        }

    df.columns = normalized_cols
    present_cols = set(df.columns)

    missing_cols = sorted(REQUIRED_COLUMNS - present_cols)
    extra_cols = sorted(present_cols - ALL_ALLOWED_COLUMNS)

    if missing_cols or extra_cols:
        reasons = []
        if missing_cols:
            reasons.append("brak wymaganych kolumn")
        if extra_cols:
            reasons.append(f"niedozwolone / nieznane kolumny w nagłówku: {', '.join(extra_cols)}")
        return {
            "success": False,
            "error": f"Nieprawidłowa struktura tabeli ({'; '.join(reasons)}). Porównaj nagłówki ze wzorem w zakładce 'Wzór tabeli i informacje o AED'.",
            "missing_columns": missing_cols,
            "extra_columns": extra_cols,
        }

    valid_records: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for idx, row in df.iterrows():
        excel_row_num = int(idx) + 2
        row_dict = {
            k: (None if isinstance(v, float) and math.isnan(v) else v)
            for k, v in row.to_dict().items()
        }

        row_errors_start = len(errors)

        dostepnosc_val = str(row_dict.get("dostepnosc") or "").strip().lower()
        godziny_val = str(row_dict.get("godziny_otwarcia") or "").strip()
        if dostepnosc_val in {"godziny_otwarcia", "ograniczona"} and not godziny_val:
            errors.append({
                "wiersz": excel_row_num,
                "pole": "godziny_otwarcia",
                "komunikat": "Dla dostępności innej niż '24/7' pole godziny_otwarcia jest obowiązkowe."
            })

        try:
            record = AEDRecord(**row_dict)
            if len(errors) == row_errors_start:
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
        "success": len(errors) == 0 and len(clean_df) > 0,
        "total_rows": len(df),
        "valid_rows": len(clean_df),
        "duplicates_removed": duplicates_removed,
        "errors": errors,
        "clean_dataframe": clean_df,
    }