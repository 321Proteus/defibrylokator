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

# Nominatim: ~1 zapytanie/s, więc plik z tysiącami wierszy bez współrzędnych blokowałby żądanie na godziny.
MAX_GEOCODED_ROWS = int(os.environ.get("MAX_GEOCODED_ROWS", "100"))
POSTCODE_RE = re.compile(r"\b\d{2}-\d{3}\b")
ADDRESS_NOT_FOUND_MSG = (
    "Nie udało się ustalić współrzędnych dla tego adresu. Sprawdź pisownię (ulica i numer, miasto) "
    "lub wpisz współrzędne ręcznie."
)


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


# kod_pocztowy, szerokosc_geo i dlugosc_geo można pominąć: uzupełniamy je z adresu (geokodowanie).
REQUIRED_COLUMNS = {
    "nazwa_obiektu",
    "miasto",
    "ulica_i_numer",
    "opis_lokalizacji",
    "dostepnosc",
    "status",
    "dysponent",
}


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

    for optional in ("kod_pocztowy", "szerokosc_geo", "dlugosc_geo"):
        if optional not in df.columns:
            df[optional] = None
    df["kod_pocztowy"] = df["kod_pocztowy"].astype(object)
    df["szerokosc_geo"] = df["szerokosc_geo"].astype(object)
    df["dlugosc_geo"] = df["dlugosc_geo"].astype(object)

    missing_cols = REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        return {
            "success": False,
            "error": "Brak wymaganych kolumn w pliku Excel.",
            "missing_columns": list(missing_cols),
        }

    valid_records: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    geocoded_rows = 0

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

        lookups_needed = _blank(row_dict.get("szerokosc_geo")) or _blank(row_dict.get("dlugosc_geo")) or _blank(row_dict.get("kod_pocztowy"))
        if lookups_needed and not (_blank(row_dict.get("ulica_i_numer")) or _blank(row_dict.get("miasto"))):
            if geocoded_rows >= MAX_GEOCODED_ROWS:
                errors.append({
                    "wiersz": excel_row_num,
                    "pole": "ulica_i_numer",
                    "komunikat": f"Przekroczono limit automatycznego uzupełniania współrzędnych i kodu pocztowego "
                                 f"({MAX_GEOCODED_ROWS} wierszy na plik). Uzupełnij te kolumny w pliku.",
                })
                continue
            geocoded_rows += 1

            if _blank(row_dict.get("szerokosc_geo")) or _blank(row_dict.get("dlugosc_geo")):
                coords = find_coordinates(str(row_dict["ulica_i_numer"]), str(row_dict["miasto"]))
                if coords is None:
                    errors.append({"wiersz": excel_row_num, "pole": "ulica_i_numer", "komunikat": ADDRESS_NOT_FOUND_MSG})
                    continue
                row_dict["szerokosc_geo"], row_dict["dlugosc_geo"] = round(coords[0], 6), round(coords[1], 6)

            if _blank(row_dict.get("kod_pocztowy")):
                try:
                    postcode = find_postcode(float(row_dict["szerokosc_geo"]), float(row_dict["dlugosc_geo"]))
                except (TypeError, ValueError):
                    postcode = None  # niepoprawne współrzędne zgłosi walidacja poniżej
                if postcode is None and not _blank(row_dict.get("szerokosc_geo")):
                    errors.append({
                        "wiersz": excel_row_num,
                        "pole": "kod_pocztowy",
                        "komunikat": "Nie udało się ustalić kodu pocztowego. Wpisz go ręcznie (format 00-000).",
                    })
                    continue
                row_dict["kod_pocztowy"] = postcode

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