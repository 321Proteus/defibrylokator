"""aed_db: pure mapping helpers always; database behaviour only when TEST_DATABASE_URL points at a Postgres
(a throw-away schema is created and dropped, nothing else is touched), e.g.
    TEST_DATABASE_URL=postgresql://mcod:mcod@localhost:5432/mcod pytest
"""

import os

import pytest

import aed_db
from conftest import good_row, needs_db

# ---------- pure helpers ----------


@pytest.mark.parametrize("street,post,city,expected", [
    ("ul. Rakowiecka 25", "02-517", "Warszawa", "ul. Rakowiecka 25, 02-517 Warszawa"),
    ("ul. Rakowiecka 25", None, "Warszawa", "ul. Rakowiecka 25, Warszawa"),
    ("ul. Rakowiecka 25", "", "", "ul. Rakowiecka 25"),
    (None, None, None, None),
    ("  ", float("nan"), "", None),
])
def test_build_address(street, post, city, expected):
    assert aed_db.build_address(street, post, city) == expected


@pytest.mark.parametrize("adres,expected", [
    ("ul. Rakowiecka 25, 02-517 Warszawa", ("ul. Rakowiecka 25", "02-517", "Warszawa")),
    ("ul. Rakowiecka 25, Warszawa", ("ul. Rakowiecka 25", "", "Warszawa")),
    ("ul. Rakowiecka 25", ("ul. Rakowiecka 25", "", "")),
    (None, ("", "", "")),
])
def test_split_address_roundtrip(adres, expected):
    assert aed_db.split_address(adres) == expected


def test_slug_and_record_key_isolate_organisations():
    assert aed_db._slug("Urząd m.st. Łódź") == "urzad-m-st-lodz"
    assert aed_db._slug("???") == "x"
    assert aed_db.record_key("PKP S.A.", "1") == "pkp-s-a:1"
    assert aed_db.record_key("PKP S.A.", "1") != aed_db.record_key("Urząd Miasta", "1")
    assert aed_db.record_key("PKP", None) is None


def test_availability_map_covers_form_values():
    from validator import ALLOWED_AVAILABILITIES
    assert set(ALLOWED_AVAILABILITIES) <= set(aed_db.AVAILABILITY_MAP)
    assert set(aed_db.AVAILABILITY_MAP.values()) <= {"publiczna", "klienci", "zezwolenie", "dozwolona", "prywatna", "nieznana"}


def test_enabled_depends_on_configuration(monkeypatch):
    assert not aed_db.enabled()
    monkeypatch.setenv("DB_HOST", "h")
    assert aed_db.enabled()


def test_dotenv_loader_parses_values_and_respects_real_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# komentarz\nDB_HOST=h.example\nDB_PASSWORD="p@ss#1"\nDB_USER=real\n\nbroken line\n', encoding="utf-8")
    monkeypatch.setenv("DB_USER", "from-shell")
    aed_db.load_dotenv(env)
    assert os.environ["DB_HOST"] == "h.example"
    assert os.environ["DB_PASSWORD"] == "p@ss#1"  # special characters need no URL-encoding
    assert os.environ["DB_USER"] == "from-shell"  # the real environment wins


# ---------- database ----------

def rows(sql, *args):
    with aed_db._connect() as conn:
        return conn.execute(sql, args).fetchall()


@needs_db
def test_save_maps_record_to_table(db):
    aed_db.save_records([good_row(kontakt_telefon="500 600 700")])
    (r,) = rows("SELECT zrodlo, nazwa, adres, gdzie_dokladnie, dostepnosc, godziny_otwarcia, status, telefon FROM defibrylatory")
    assert r == ("zgloszenia", "Urząd Gminy", "ul. Piotrkowska 1, 90-001 Łódź", "Parter przy recepcji",
                 "publiczna", "24/7", "sprawny", "500 600 700")


@needs_db
def test_resubmitting_same_place_updates_instead_of_duplicating(db):
    aed_db.save_records([good_row(status="sprawny")])
    aed_db.save_records([good_row(status="w_serwisie")])
    assert rows("SELECT status FROM defibrylatory") == [("w_serwisie",)]


@needs_db
def test_same_external_id_from_two_organisations_does_not_collide(db):
    aed_db.save_records([
        good_row("Szkoła A", id_zewnetrzne="1", dysponent="Gmina A", szerokosc_geo=51.1),
        good_row("Szkoła B", id_zewnetrzne="1", dysponent="Gmina B", szerokosc_geo=51.2),
    ])
    assert sorted(r[0] for r in rows("SELECT nazwa FROM defibrylatory")) == ["Szkoła A", "Szkoła B"]


@needs_db
def test_same_organisation_reusing_its_id_updates_its_own_row(db):
    aed_db.save_records([good_row("Szkoła A", id_zewnetrzne="1", dysponent="Gmina A", status="sprawny")])
    aed_db.save_records([good_row("Szkoła A", id_zewnetrzne="1", dysponent="Gmina A", szerokosc_geo=51.5, status="nieaktywny")])
    assert rows("SELECT status FROM defibrylatory") == [("nieaktywny",)]


@needs_db
def test_operators_are_reused_case_insensitively(db):
    aed_db.save_records([good_row("A", dysponent="Gmina X", szerokosc_geo=51.1),
                         good_row("B", dysponent=" gmina x ", szerokosc_geo=51.2)])
    assert rows("SELECT count(*) FROM operatorzy") == [(1,)]
    assert rows("SELECT count(DISTINCT operator_id) FROM defibrylatory") == [(1,)]


@needs_db
def test_batch_is_atomic(db):
    bad = good_row("B", szerokosc_geo=99.0)  # violates the table's CHECK on lat
    with pytest.raises(Exception):
        aed_db.save_records([good_row("A"), bad])
    assert rows("SELECT count(*) FROM defibrylatory") == [(0,)]


@needs_db
def test_load_items_shape_and_cache_invalidation(db):
    assert aed_db.load_items() == []
    fp_empty = aed_db.data_fingerprint()
    aed_db.save_records([good_row(kontakt_telefon="500600700")])  # save must drop the cache
    (item,) = aed_db.load_items()
    assert item["nazwa_obiektu"] == "Urząd Gminy" and item["miasto"] == "Łódź" and item["kod_pocztowy"] == "90-001"
    assert item["ulica_i_numer"] == "ul. Piotrkowska 1" and item["dysponent"] == "Miasto Łódź"
    assert item["id_zewnetrzne"].startswith("ZGL-")
    assert aed_db.data_fingerprint() != fp_empty


@needs_db
def test_imported_rows_from_other_sources_are_listed_with_prefixed_ids(db):
    with aed_db._connect() as conn:
        conn.execute("INSERT INTO defibrylatory (zrodlo, zrodlo_id, lat, lon, nazwa) VALUES ('osm','node/1',52.0,19.0,'X')")
    aed_db._invalidate_cache()
    assert [i["id_zewnetrzne"] for i in aed_db.load_items()] == ["osm:node/1"]
