import pytest

import validator
from conftest import HEADERS, good_row
from validator import AEDRecord, normalize_phone, pl_plural, process_and_validate_excel, validate_single_submission


def fields(result):
    return {(e.get("wiersz"), e["pole"]) for e in result["errors"]}


# ---------- Excel batch ----------

def test_valid_file_is_accepted(make_xlsx, good):
    result = process_and_validate_excel(make_xlsx([good(), good("Szkoła Podstawowa")]))
    assert result["success"] and result["valid_rows"] == 2 and result["errors"] == []
    assert list(result["clean_dataframe"]["nazwa_obiektu"]) == ["Urząd Gminy", "Szkoła Podstawowa"]


def test_comma_decimal_and_formatted_phone(make_xlsx, good):
    row = good(szerokosc_geo="51,77", dlugosc_geo="19,46", kontakt_telefon="+48 22 443 62 00")
    result = process_and_validate_excel(make_xlsx([row]))
    assert result["success"]
    assert result["clean_dataframe"].iloc[0]["szerokosc_geo"] == pytest.approx(51.77)


def test_numeric_external_id_is_accepted_as_text(make_xlsx, good):
    result = process_and_validate_excel(make_xlsx([good(id_zewnetrzne=12345)]))
    assert result["success"]
    assert result["clean_dataframe"].iloc[0]["id_zewnetrzne"] == "12345"


def test_opening_hours_cell_kept_as_text(make_xlsx, good):
    row = good(dostepnosc="godziny_otwarcia", godziny_otwarcia="Pn-Pt 8-16")
    result = process_and_validate_excel(make_xlsx([row]))
    assert result["success"] and result["clean_dataframe"].iloc[0]["godziny_otwarcia"] == "Pn-Pt 8-16"


def test_headers_are_case_and_space_insensitive(make_xlsx, good):
    path = make_xlsx([good()], headers=[f" {h.upper()} " for h in HEADERS], keys=HEADERS)
    assert process_and_validate_excel(path)["success"]


def test_blank_rows_are_skipped_and_row_numbers_stay_true(make_xlsx, good):
    path = make_xlsx([good(), [None] * 13, good("Biblioteka", status="zepsuty")])
    result = process_and_validate_excel(path)
    assert result["total_rows"] == 2
    assert (4, "status") in fields(result)  # third data row sits in Excel row 4


def test_missing_coordinates_rejected_file(make_xlsx, good):
    result = process_and_validate_excel(make_xlsx([good(szerokosc_geo=None, dlugosc_geo=None)]))
    assert not result["success"]
    assert {(2, "szerokosc_geo"), (2, "dlugosc_geo")} <= fields(result)


def test_one_bad_row_rejects_whole_file(make_xlsx, good):
    result = process_and_validate_excel(make_xlsx([good(), good("Biblioteka", status="zepsuty")]))
    assert not result["success"] and result["valid_rows"] == 1  # nothing may be stored by the caller


def test_bad_values_report_each_field(make_xlsx, good):
    row = good(nazwa_obiektu="123", miasto="Łódź2", status="zepsuty", szerokosc_geo=60, kontakt_telefon="12")
    result = process_and_validate_excel(make_xlsx([row]))
    assert {(2, "nazwa_obiektu"), (2, "miasto"), (2, "status"), (2, "szerokosc_geo"), (2, "kontakt_telefon")} <= fields(result)


@pytest.mark.parametrize("dostepnosc", ["ograniczona", "godziny_otwarcia"])
def test_hours_required_unless_24_7(make_xlsx, good, dostepnosc):
    result = process_and_validate_excel(make_xlsx([good(dostepnosc=dostepnosc)]))
    assert (2, "godziny_otwarcia") in fields(result)


def test_extra_column_rejected(make_xlsx, good):
    result = process_and_validate_excel(make_xlsx([good()], headers=HEADERS + ["foo"], keys=HEADERS + ["foo"]))
    assert not result["success"] and result["extra_columns"] == ["foo"]


def test_missing_required_column_rejected(make_xlsx, good):
    headers = [h for h in HEADERS if h != "status"]
    result = process_and_validate_excel(make_xlsx([good()], headers=headers))
    assert result["missing_columns"] == ["status"]


def test_header_only_file_has_zero_rows(make_xlsx):
    result = process_and_validate_excel(make_xlsx([]))
    assert result["total_rows"] == 0 and not result["success"]


def test_duplicate_header_is_reported_as_duplicate(make_xlsx, good):
    result = process_and_validate_excel(make_xlsx([good()], headers=HEADERS + ["nazwa_obiektu"], keys=HEADERS + ["nazwa_obiektu"]))
    assert "powtarzają" in result["error"] and "nazwa_obiektu" in result["error"]


def test_headerless_column_with_data_is_not_silently_dropped(make_xlsx, good):
    path = make_xlsx([good() | {"x": "dane"}], headers=HEADERS + [None], keys=HEADERS + ["x"])
    result = process_and_validate_excel(path)
    assert not result["success"] and "bez nagłówka" in result["error"]


def test_empty_headerless_column_is_ignored(make_xlsx, good):
    path = make_xlsx([good() | {"x": None}], headers=HEADERS + [None], keys=HEADERS + ["x"])
    assert process_and_validate_excel(path)["success"]


def test_duplicate_rows_collapse(make_xlsx, good):
    result = process_and_validate_excel(make_xlsx([good(), good()]))
    assert result["valid_rows"] == 1 and result["duplicates_removed"] == 1


def test_row_limit(make_xlsx, good, monkeypatch):
    monkeypatch.setattr(validator, "MAX_BATCH_ROWS", 2)
    result = process_and_validate_excel(make_xlsx([good(f"Obiekt {c}") for c in "ABC"]))
    assert "najwyżej 2" in result["error"]


def test_not_an_excel_file(tmp_path):
    bad = tmp_path / "x.xlsx"
    bad.write_text("to nie jest excel")
    assert "Nie udało się odczytać" in process_and_validate_excel(str(bad))["error"]


# ---------- single form ----------

FORM = dict(nazwa_obiektu="Urząd Dzielnicy", miasto="Warszawa", adres="ul. Rakowiecka 25",
            opis_lokalizacji="Parter przy recepcji", status="sprawny")


def test_form_minimal_payload_is_valid_without_coordinates():
    draft, errors = validate_single_submission(FORM)
    assert errors == [] and draft.szerokosc_geo is None and draft.dostepnosc is None


def test_form_blank_strings_are_treated_as_missing_optionals():
    draft, errors = validate_single_submission(FORM | {"dostepnosc": "", "kontakt_telefon": "", "kod_pocztowy": "",
                                                       "szerokosc_geo": "", "dlugosc_geo": ""})
    assert errors == []


def test_form_errors_use_form_field_names():
    _, errors = validate_single_submission(FORM | {"adres": "12", "nazwa_obiektu": "ab", "status": ""})
    assert {e["pole"] for e in errors} == {"adres", "nazwa_obiektu", "status"}


def test_form_requires_both_coordinates_or_none():
    _, errors = validate_single_submission(FORM | {"szerokosc_geo": "52.2"})
    assert [e["pole"] for e in errors] == ["szerokosc_geo"]


def test_form_coordinates_out_of_poland():
    _, errors = validate_single_submission(FORM | {"szerokosc_geo": "60", "dlugosc_geo": "20"})
    assert [e["pole"] for e in errors] == ["szerokosc_geo"]


def test_form_rejects_invalid_availability_and_postcode_and_phone():
    _, errors = validate_single_submission(FORM | {"dostepnosc": "zawsze", "kod_pocztowy": "123", "kontakt_telefon": "12"})
    assert {e["pole"] for e in errors} == {"dostepnosc", "kod_pocztowy", "kontakt_telefon"}


def test_form_and_batch_share_rules():
    """A value rejected in Excel is rejected in the form for the same reason (one rule set)."""
    form_msg = validate_single_submission(FORM | {"miasto": "Łódź2"})[1][0]["komunikat"]
    with pytest.raises(Exception) as exc:
        AEDRecord(**good_row(miasto="Łódź2"))
    assert form_msg in str(exc.value)


# ---------- helpers ----------

@pytest.mark.parametrize("raw,ok", [
    ("500 600 700", True), ("+48 22 443 62 00", True), ("0048 500600700", True), ("(22) 443-62-00", True),
    ("12", False), ("000 000 000", False), ("abc", False),
])
def test_phone_normalization(raw, ok):
    if ok:
        assert normalize_phone(raw) == raw.strip()
    else:
        with pytest.raises(ValueError):
            normalize_phone(raw)


@pytest.mark.parametrize("n,word", [
    (1, "rekord"), (2, "rekordy"), (4, "rekordy"), (5, "rekordów"), (12, "rekordów"), (14, "rekordów"),
    (22, "rekordy"), (25, "rekordów"), (112, "rekordów"), (0, "rekordów"),
])
def test_polish_plural(n, word):
    assert pl_plural(n, "rekord", "rekordy", "rekordów") == word
