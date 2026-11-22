"""Walidacja zgłoszeń AED: jedno miejsce z regułami dla pliku Excel (wsad) i formularza (pojedynczy AED).

Reguły (statusy, dostępność, telefon, miasto, zakresy współrzędnych, minimalne długości) żyją tylko tutaj;
main.py niczego nie waliduje samodzielnie. Rekord ma schemat "CSV/Excel" (nazwy kolumn po polsku),
który aed_db.py mapuje na tabelę `defibrylatory`.
"""

import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # geocode.py leży w katalogu głównym repozytorium
import geocode  # noqa: E402

LAT_RANGE = (49.0, 54.9)
LON_RANGE = (14.1, 24.2)
ALLOWED_STATUSES = ("sprawny", "w_serwisie", "nieaktywny")
ALLOWED_AVAILABILITIES = ("24/7", "godziny_otwarcia", "ograniczona")
INDIVIDUAL_SUBMITTER = "Zgłoszenie indywidualne"  # dysponent rekordów dodanych formularzem
MAX_BATCH_ROWS = 5000  # górny limit wierszy w jednym pliku (endpoint jest publiczny)

POSTCODE_RE = re.compile(r"\b\d{2}-\d{3}\b")
CITY_RE = re.compile(r"^[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż\s\-]{2,60}$")
HAS_LETTER_RE = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]")
PL_PHONE_RE = re.compile(r"[1-8]\d{8}")
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

# Zgodnie z tabelą we wzorze na stronie: kolumny z gwiazdką.
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

# Nazwa pola w formularzu pojedynczego AED -> nazwa kolumny w schemacie (i odwrotnie przy raportowaniu błędów).
FORM_FIELD_ALIASES = {"adres": "ulica_i_numer"}
_COLUMN_TO_FORM_FIELD = {v: k for k, v in FORM_FIELD_ALIASES.items()}


def pl_plural(n: int, one: str, few: str, many: str) -> str:
    """Polska odmiana rzeczownika przy liczbie: 1 rekord, 2 rekordy, 5 rekordów, 22 rekordy."""
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or str(value).strip() == ""


def _cell_text(value: Any) -> Optional[str]:
    """Wartość komórki jako tekst: puste -> None, liczby całkowite bez '.0' (np. ID numeryczne z Excela)."""
    if _blank(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize_phone(value: Any) -> Optional[str]:
    """Telefon bez znaków formatowania i bez +48/0048; ValueError, gdy to nie polski numer 9-cyfrowy."""
    if _blank(value):
        return None
    digits = re.sub(r"[\s\-\(\)]", "", str(value).strip())
    if digits.startswith("+48"):
        digits = digits[3:]
    elif digits.startswith("0048"):
        digits = digits[4:]
    if not PL_PHONE_RE.fullmatch(digits):
        raise ValueError("Niepoprawny polski numer telefonu (wymagane 9 cyfr, np. +48 22 443 62 00 lub 500 600 700).")
    return str(value).strip()


class AEDRecord(BaseModel):
    """Pełny rekord gotowy do zapisu (wsad Excel i formularz po uzupełnieniu współrzędnych)."""

    id_zewnetrzne: Optional[str] = Field(None, description="ID w bazie dysponenta")
    nazwa_obiektu: str = Field(..., min_length=3, description="Np. Urząd Gminy, Dworzec PKP")
    miasto: str = Field(..., min_length=2)
    kod_pocztowy: str = Field(..., pattern=r"^\d{2}-\d{3}$")
    ulica_i_numer: str = Field(..., min_length=3)
    opis_lokalizacji: str = Field(..., min_length=4, description="Np. Parter, przy recepcji na ścianie")
    szerokosc_geo: float = Field(..., ge=LAT_RANGE[0], le=LAT_RANGE[1], description="Latitude (granice Polski)")
    dlugosc_geo: float = Field(..., ge=LON_RANGE[0], le=LON_RANGE[1], description="Longitude (granice Polski)")
    dostepnosc: str = Field(..., description=", ".join(ALLOWED_AVAILABILITIES))
    godziny_otwarcia: Optional[str] = Field(None, description="Wymagane, jeśli dostępność != 24/7")
    status: str = Field(..., description=", ".join(ALLOWED_STATUSES))
    dysponent: str = Field(..., min_length=2, description="Nazwa instytucji odpowiedzialnej")
    kontakt_telefon: Optional[str] = None

    @field_validator("id_zewnetrzne", "godziny_otwarcia", mode="before")
    @classmethod
    def cell_to_text(cls, v: Any) -> Optional[str]:
        return _cell_text(v)

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
        return None if v is None else str(v).strip()

    @field_validator("szerokosc_geo", "dlugosc_geo", mode="before")
    @classmethod
    def parse_coord(cls, v: Any) -> Any:
        if _blank(v):
            raise ValueError("Brak wymaganej współrzędnej geograficznej.")
        if isinstance(v, str):
            v = v.strip().replace(",", ".")
        return v

    @field_validator("dostepnosc", mode="before")
    @classmethod
    def validate_dostepnosc(cls, v: Any) -> str:
        if _blank(v):
            raise ValueError(f"Brak wartości dostępności (dozwolone: {', '.join(ALLOWED_AVAILABILITIES)}).")
        v_clean = str(v).strip().lower()
        if v_clean not in ALLOWED_AVAILABILITIES:
            raise ValueError(f"Niedozwolona wartość '{v}'. Dozwolone opcje: {', '.join(ALLOWED_AVAILABILITIES)}.")
        return v_clean

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, v: Any) -> str:
        if _blank(v):
            raise ValueError(f"Brak wartości statusu (dozwolone: {', '.join(ALLOWED_STATUSES)}).")
        v_clean = str(v).strip().lower()
        if v_clean not in ALLOWED_STATUSES:
            raise ValueError(f"Niedozwolony status '{v}'. Dozwolone opcje: {', '.join(ALLOWED_STATUSES)}.")
        return v_clean

    @field_validator("kontakt_telefon", mode="before")
    @classmethod
    def validate_phone(cls, v: Any) -> Optional[str]:
        return normalize_phone(v)


class AEDDraft(AEDRecord):
    """Zgłoszenie z formularza: współrzędne, kod pocztowy i dostępność są opcjonalne.

    Brakujące współrzędne i kod uzupełnia serwer z adresu (geokodowanie); potem rekord przechodzi
    jeszcze raz przez rygorystyczny AEDRecord.
    """

    kod_pocztowy: Optional[str] = Field(None, pattern=r"^\d{2}-\d{3}$")
    szerokosc_geo: Optional[float] = Field(None, ge=LAT_RANGE[0], le=LAT_RANGE[1])
    dlugosc_geo: Optional[float] = Field(None, ge=LON_RANGE[0], le=LON_RANGE[1])
    dostepnosc: Optional[str] = None

    @field_validator("kod_pocztowy", mode="before")
    @classmethod
    def clean_postcode(cls, v: Any) -> Any:
        return None if _blank(v) else str(v).strip()

    @field_validator("szerokosc_geo", "dlugosc_geo", mode="before")
    @classmethod
    def parse_coord(cls, v: Any) -> Any:
        if _blank(v):
            return None
        return v.strip().replace(",", ".") if isinstance(v, str) else v

    @field_validator("dostepnosc", mode="before")
    @classmethod
    def validate_dostepnosc(cls, v: Any) -> Optional[str]:
        return None if _blank(v) else AEDRecord.validate_dostepnosc(v)

    @model_validator(mode="after")
    def both_coordinates_or_none(self) -> "AEDDraft":
        if (self.szerokosc_geo is None) != (self.dlugosc_geo is None):
            raise ValueError("Podaj obie współrzędne (szerokość i długość geograficzną) albo żadnej.")
        return self


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
        return f"Tekst jest za krótki (wymagane minimum {ctx.get('min_length', 2)} znaki)."
    if err_type == "string_pattern_mismatch":
        return "Niepoprawny format danych (kod pocztowy musi mieć format 00-000)."
    if err_type == "greater_than_equal":
        return f"Wartość musi być większa lub równa {ctx.get('ge')} (poza granicami Polski)."
    if err_type == "less_than_equal":
        return f"Wartość musi być mniejsza lub równa {ctx.get('le')} (poza granicami Polski)."
    if err_type == "missing":
        return "Brak wymaganej wartości w komórce."
    return msg


def _hours_error(dostepnosc: Any, godziny_otwarcia: Any) -> Optional[str]:
    if str(dostepnosc or "").strip().lower() in ("godziny_otwarcia", "ograniczona") and _blank(godziny_otwarcia):
        return "Dla dostępności innej niż '24/7' pole godziny_otwarcia jest obowiązkowe."
    return None


def errors_from(exc: ValidationError, **extra: Any) -> List[Dict[str, Any]]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"])
        out.append({**extra, "pole": loc or "rekord", "komunikat": translate_pydantic_error(err)})
    return out


def validate_single_submission(payload: Dict[str, Any]) -> tuple:
    """Waliduje zgłoszenie z formularza. Zwraca (AEDDraft | None, błędy); pola błędów mają nazwy z formularza."""
    data = {
        "nazwa_obiektu": payload.get("nazwa_obiektu"),
        "miasto": payload.get("miasto"),
        "ulica_i_numer": payload.get("adres"),
        "opis_lokalizacji": payload.get("opis_lokalizacji"),
        "status": payload.get("status"),
        "dostepnosc": payload.get("dostepnosc"),
        "kontakt_telefon": payload.get("kontakt_telefon"),
        "kod_pocztowy": payload.get("kod_pocztowy"),
        "szerokosc_geo": payload.get("szerokosc_geo"),
        "dlugosc_geo": payload.get("dlugosc_geo"),
        "dysponent": INDIVIDUAL_SUBMITTER,
    }
    try:
        draft = AEDDraft(**data)
    except ValidationError as exc:
        errors = errors_from(exc)
        for error in errors:
            error["pole"] = _COLUMN_TO_FORM_FIELD.get(error["pole"], error["pole"])
            if error["pole"] == "rekord":  # błąd całego rekordu (np. tylko jedna współrzędna)
                error["pole"] = "szerokosc_geo"
        return None, errors
    return draft, []


def _read_sheet(file_path: str) -> pd.DataFrame:
    """Pierwszy arkusz jako tekst: ID, telefony i godziny z Excela nie zamieniają się w liczby/daty."""
    excel_file = pd.ExcelFile(file_path)
    if not excel_file.sheet_names:
        raise ValueError("Plik Excel nie zawiera żadnych arkuszy.")
    return excel_file.parse(sheet_name=0, dtype=str, keep_default_na=False)


def process_and_validate_excel(file_path: str) -> Dict[str, Any]:
    try:
        df = _read_sheet(file_path)
    except Exception as e:
        return {"success": False, "error": f"Nie udało się odczytać pliku Excel: {str(e)}"}

    df = df.apply(lambda column: column.map(_cell_text)).astype(object)  # puste komórki -> None
    df = df.dropna(how="all")  # całkowicie puste wiersze (zachowują oryginalną numerację dla komunikatów)

    # Kolumna bez nagłówka: pusta jest ignorowana (ślad po formatowaniu), z danymi - błąd, a nie ciche pominięcie.
    unnamed = [c for c in df.columns if re.match(r"^Unnamed:\s*\d+$", str(c))]
    unnamed_with_data = [c for c in unnamed if df[c].notna().any()]
    df = df.drop(columns=[c for c in unnamed if c not in unnamed_with_data])

    normalized_cols = [str(col).strip().lower() for col in df.columns]
    present_cols = set(normalized_cols)

    # pandas zmienia powtórzony nagłówek "a" na "a.1"; wykrywamy to tu, żeby komunikat był czytelny.
    duplicated = sorted({
        m.group(1) for c in normalized_cols
        if (m := re.fullmatch(r"(.+)\.\d+", c)) and m.group(1) in present_cols
    } | {c for c in present_cols if normalized_cols.count(c) > 1})
    if duplicated:
        return {
            "success": False,
            "error": f"W pliku Excel powtarzają się nagłówki kolumn: {', '.join(duplicated)}. Usuń duplikaty.",
            "missing_columns": [],
            "extra_columns": [],
        }

    df.columns = normalized_cols
    missing_cols = sorted(REQUIRED_COLUMNS - present_cols)
    extra_cols = sorted(c for c in present_cols - ALL_ALLOWED_COLUMNS if not re.fullmatch(r"unnamed:\s*\d+", c))
    extra_cols += [f"(kolumna {i + 1} bez nagłówka)" for i, c in enumerate(df.columns) if str(c).startswith("unnamed:")]

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

    if len(df) > MAX_BATCH_ROWS:
        return {
            "success": False,
            "error": f"Plik ma {len(df)} wierszy; w jednym pliku można przesłać najwyżej {MAX_BATCH_ROWS}. Podziel go na części.",
            "missing_columns": [],
            "extra_columns": [],
        }

    valid_records: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for idx, row in df.iterrows():
        excel_row_num = int(idx) + 2  # nagłówek to wiersz 1
        row_dict = row.to_dict()
        row_errors_start = len(errors)

        hours_error = _hours_error(row_dict.get("dostepnosc"), row_dict.get("godziny_otwarcia"))
        if hours_error:
            errors.append({"wiersz": excel_row_num, "pole": "godziny_otwarcia", "komunikat": hours_error})

        try:
            record = AEDRecord(**row_dict)
        except ValidationError as exc:
            errors.extend(errors_from(exc, wiersz=excel_row_num))
            continue
        if len(errors) == row_errors_start:
            valid_records.append(record.model_dump())

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
