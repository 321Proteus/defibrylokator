import json

import pytest

import import_aed_helpers as helpers
import import_aed_osm as osm
import import_aed_sopot as sopot
from conftest import needs_db


# ---------- shared helpers ----------

def test_text_and_address_helpers():
    assert helpers.text_or_none("  x ") == "x" and helpers.text_or_none("  ") is None and helpers.text_or_none(None) is None
    assert helpers.make_address("ul.", "Piotrkowska", "1", "90-001", "Łódź") == "ul. Piotrkowska 1, 90-001 Łódź"
    assert helpers.make_address(None, None, None, None, None) is None


@pytest.mark.parametrize("text,expected", [("wewnątrz budynku", True), ("Zewnętrzny", False), ("", None), (None, None)])
def test_inside_from_text(text, expected):
    assert helpers.inside_from_text(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("Publiczna", "publiczna"), ("prywatna", "prywatna"), ("dla klientów", "klienci"), ("", "nieznana"), (None, "nieznana"),
])
def test_availability_from_public_access(text, expected):
    assert helpers.availability_from_public_access(text) == expected


def test_insert_records_without_database_config_fails_clearly():
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        helpers.insert_records([{"zrodlo": "x", "zrodlo_id": "1", "lat": 52.0, "lon": 19.0}])


def test_insert_records_refuses_rows_without_source_id():
    with pytest.raises(ValueError, match="zrodlo_id"):
        helpers.insert_records([{"zrodlo": "x", "zrodlo_id": None, "lat": 52.0, "lon": 19.0}])


# ---------- Sopot ----------

def test_sopot_mojibake_is_repaired_and_clean_text_left_alone():
    broken = [{"Nazwa ulicy": "Tadeusza KoĹ›ciuszki", "MiejscowoĹ›Ä‡": "Sopot"}]
    assert sopot.repair_mojibake(broken) == [{"Nazwa ulicy": "Tadeusza Kościuszki", "Miejscowość": "Sopot"}]
    clean = [{"Nazwa ulicy": "Kościuszki"}]
    assert sopot.repair_mojibake(clean) == clean


def test_sopot_geocode_candidates_clean_prefix_and_ranges_but_keep_postcode():
    candidates = sopot.geocode_candidates("ul. Tadeusza Kościuszki 25-27, 81-704 Sopot")
    assert candidates == [
        "ul. Tadeusza Kościuszki 25-27, 81-704 Sopot",
        "Tadeusza Kościuszki 25, 81-704 Sopot",  # the postcode "81-704" must not be treated as a range
        "Tadeusza Kościuszki 25, Sopot",
    ]


# ---------- OpenAEDMap / OSM ----------

def feature(**props):
    base = {"@osm_id": 1, "@osm_type": "node", "emergency": "defibrillator"}
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [19.5, 49.6]}, "properties": base | props}


def test_osm_record_core_mapping():
    r = osm.build_record(feature(**{
        "name": "Schronisko", "defibrillator:location": "Na ścianie", "level": "1", "indoor": "yes", "access": "customers",
        "opening_hours": "24/7", "emergency:phone": "112", "phone": "+48 33 877 51 05", "check_date": "2024-05-01",
        "image": "https://example.org/a.jpg", "note": "uwaga", "fixme": "sprawdzić",
    }))
    assert (r["zrodlo"], r["zrodlo_id"], r["lat"], r["lon"]) == ("osm", "node/1", 49.6, 19.5)
    assert r["nazwa"] == "Schronisko" and r["gdzie_dokladnie"] == "Na ścianie" and r["pietro"] == "1"
    assert r["wewnatrz"] is True and r["dostepnosc"] == "klienci" and r["godziny_otwarcia"] == "24/7"
    assert r["telefon"] == "112"  # the number at the device beats the operator's
    assert r["ostatnia_weryfikacja"] == "2024-05-01" and r["zdjecie_url"] == "https://example.org/a.jpg"
    assert r["uwagi"] == "uwaga | fixme: sprawdzić"
    assert r["inne"]["@osm_id"] == 1  # raw tags are preserved


@pytest.mark.parametrize("access,expected", [
    ("yes", "publiczna"), ("permissive", "dozwolona"), ("customers", "klienci"), ("permit", "zezwolenie"),
    ("private", "prywatna"), ("no", "prywatna"), ("YES", "publiczna"), ("weird", "nieznana"), (None, "nieznana"),
])
def test_osm_access_mapping(access, expected):
    props = {} if access is None else {"access": access}
    assert osm.build_record(feature(**props))["dostepnosc"] == expected


def test_osm_location_falls_back_through_language_variants():
    assert osm.build_record(feature(**{"defibrillator:location:pl": "PL", "defibrillator:location": "x"}))["gdzie_dokladnie"] == "PL"
    assert osm.build_record(feature(**{"description": "opis"}))["gdzie_dokladnie"] == "opis"


@pytest.mark.parametrize("value,expected", [("2024", "2024"), ("2024-05", "2024-05"), ("2024-05-01", "2024-05-01"),
                                            ("May 2024", None), ("2024/05/01", None)])
def test_osm_check_date_must_match_table_constraint(value, expected):
    assert osm.build_record(feature(check_date=value))["ostatnia_weryfikacja"] == expected


def test_osm_only_http_photos():
    assert osm.build_record(feature(image="File:Aed.jpg"))["zdjecie_url"] is None


def test_osm_indoor_values():
    assert osm.build_record(feature(indoor="no"))["wewnatrz"] is False
    assert osm.build_record(feature())["wewnatrz"] is None


def test_osm_address_from_addr_tags():
    r = osm.build_record(feature(**{"addr:street": "Długa", "addr:housenumber": "5", "addr:city": "Gdańsk"}))
    assert r["adres"] == "Długa 5, Gdańsk"


@pytest.mark.parametrize("coords", [[10.0, 52.0], [19.5, 60.0], [19.5], None])
def test_osm_points_outside_poland_or_malformed_are_skipped(coords):
    f = feature()
    f["geometry"]["coordinates"] = coords
    assert osm.build_record(f) is None


def test_osm_non_point_and_missing_id_skipped():
    f = feature()
    f["geometry"]["type"] = "Polygon"
    assert osm.build_record(f) is None
    assert osm.build_record({"geometry": {"type": "Point", "coordinates": [19.5, 49.6]}, "properties": {}}) is None


def test_osm_build_records_dedupes_and_counts(tmp_path):
    path = tmp_path / "x.geojson"
    far = feature(**{"@osm_id": 3})
    far["geometry"]["coordinates"] = [0.0, 0.0]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [feature(), feature(name="nowa wersja"), far]}))
    records, stats = osm.build_records(path)
    assert len(records) == 1 and records[0]["nazwa"] == "nowa wersja"
    assert (stats["duplicate_in_file"], stats["skipped"]) == (1, 1)


@needs_db
def test_osm_import_end_to_end_is_idempotent_and_creates_operators(db, tmp_path):
    import aed_db
    path = tmp_path / "x.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        feature(**{"@osm_id": 1, "operator": "OSP Przysucha", "operator:wikidata": "Q1"}),
        feature(**{"@osm_id": 2, "operator": " osp przysucha "}),
        feature(**{"@osm_id": 3}),
    ]}))
    for _ in range(2):  # running twice must not duplicate anything
        records, _ = osm.build_records(path)
        assert osm.resolve_operators(records) in (1, 0)
        for r in records:
            r.pop("_operator"), r.pop("_operator_wikidata")
        helpers.insert_records(records)
    with aed_db._connect() as conn:
        assert conn.execute("SELECT count(*) FROM defibrylatory WHERE zrodlo='osm'").fetchone()[0] == 3
        assert conn.execute("SELECT count(*), max(wikidata) FROM operatorzy").fetchone() == (1, "Q1")
        assert conn.execute("SELECT count(*) FROM defibrylatory WHERE operator_id IS NOT NULL").fetchone()[0] == 2
