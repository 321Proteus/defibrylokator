"""HTTP-level tests of the web app in CSV mode (no database), writing only to a temp directory."""

import gzip
import hashlib
import io
import re

import pytest
from fastapi.testclient import TestClient

import main
from conftest import good_row

FORM = dict(declaration=True, nazwa_obiektu="Urząd Dzielnicy", miasto="Warszawa", adres="ul. Rakowiecka 25",
            opis_lokalizacji="Parter przy recepcji", status="sprawny", dostepnosc="24/7",
            szerokosc_geo="52.2045", dlugosc_geo="21.0123", kod_pocztowy="02-517")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MASTER_CSV_PATH", tmp_path / "master.csv")
    monkeypatch.setattr(main, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(main, "find_postcode", lambda *a: "00-000", raising=False)
    # never reach the network geocoder
    monkeypatch.setattr(main, "find_coordinates", lambda *a: (52.1, 21.0))
    monkeypatch.setattr(main, "find_address_details", lambda *a: ("ul. Test 1, 02-517 Warszawa", "02-517"))
    return TestClient(main.app)


def post_file(client, path, declaration=True, filename="plik.xlsx"):
    with open(path, "rb") as f:
        return client.post("/upload-aed", files={"file": (filename, f)}, data={"declaration": str(declaration).lower()})


# ---------- single form ----------

def test_single_aed_is_stored_and_listed(client):
    r = client.post("/add-aed", json=FORM)
    assert r.json()["status"] == "accepted"
    (item,) = client.get("/api/aed-list").json()["items"]
    assert item["nazwa_obiektu"] == "Urząd Dzielnicy" and item["szerokosc_geo"] == 52.2045
    assert item["dysponent"] == "Zgłoszenie indywidualne" and item["kod_pocztowy"] == "02-517"


def test_single_aed_requires_declaration(client):
    assert client.post("/add-aed", json=FORM | {"declaration": False}).status_code == 400


def test_single_aed_validation_errors_use_form_field_names(client):
    r = client.post("/add-aed", json=FORM | {"nazwa_obiektu": "a", "adres": "", "status": "x"}).json()
    assert r["status"] == "validation_errors"
    assert {e["pole"] for e in r["errors"]} == {"nazwa_obiektu", "adres", "status"}
    assert client.get("/api/aed-list").json()["items"] == []  # nothing stored


def test_single_aed_fills_coordinates_and_postcode_from_address(client):
    r = client.post("/add-aed", json={k: v for k, v in FORM.items() if k not in ("szerokosc_geo", "dlugosc_geo", "kod_pocztowy")})
    assert r.json()["status"] == "accepted"
    (item,) = client.get("/api/aed-list").json()["items"]
    assert (item["szerokosc_geo"], item["dlugosc_geo"], item["kod_pocztowy"]) == (52.1, 21.0, "02-517")


def test_single_aed_unknown_address_is_a_field_error(client, monkeypatch):
    monkeypatch.setattr(main, "find_coordinates", lambda *a: None)
    r = client.post("/add-aed", json={k: v for k, v in FORM.items() if k not in ("szerokosc_geo", "dlugosc_geo", "kod_pocztowy")}).json()
    assert r["status"] == "validation_errors" and r["errors"][0]["pole"] == "adres"


def test_single_aed_resubmission_does_not_duplicate(client):
    client.post("/add-aed", json=FORM)
    client.post("/add-aed", json=FORM)
    assert len(client.get("/api/aed-list").json()["items"]) == 1


def test_single_aed_unspecified_availability_is_allowed(client):
    assert client.post("/add-aed", json=FORM | {"dostepnosc": ""}).json()["status"] == "accepted"


# ---------- Excel batch ----------

def test_upload_valid_excel(client, make_xlsx, good):
    r = post_file(client, make_xlsx([good(), good("Szkoła")])).json()
    assert r["status"] == "accepted" and "przyjęto 2 rekordy" in r["message"]
    assert len(client.get("/api/aed-list").json()["items"]) == 2


def test_upload_requires_declaration_and_excel_extension(client, make_xlsx, good):
    path = make_xlsx([good()])
    assert post_file(client, path, declaration=False).status_code == 400
    assert post_file(client, path, filename="dane.csv").status_code == 400


def test_upload_with_errors_stores_nothing(client, make_xlsx, good):
    r = post_file(client, make_xlsx([good(), good("Biblioteka", status="zepsuty")])).json()
    assert r["status"] == "validation_errors" and "1 błąd" in r["message"] and "2 wiersze" in r["message"]
    assert r["errors"][0]["wiersz"] == 3
    assert client.get("/api/aed-list").json()["items"] == []


def test_upload_wrong_structure_is_rejected_with_columns(client, make_xlsx, good):
    from conftest import HEADERS
    r = post_file(client, make_xlsx([good()], headers=HEADERS + ["foo"], keys=HEADERS + ["foo"])).json()
    assert r["status"] == "rejected" and r["extra_columns"] == ["foo"]


def test_upload_empty_sheet(client, make_xlsx):
    assert post_file(client, make_xlsx([])).json()["status"] == "rejected"


def test_failed_storage_is_a_503_not_a_thank_you(client, make_xlsx, good, monkeypatch):
    monkeypatch.setattr(main, "store_records", lambda df: (_ for _ in ()).throw(RuntimeError("db down")))
    assert post_file(client, make_xlsx([good()])).status_code == 503
    assert client.post("/add-aed", json=FORM).status_code == 503


# ---------- export, feed, static ----------

def test_export_csv_contains_stored_rows(client):
    client.post("/add-aed", json=FORM)
    r = client.get("/dane-gov-export.csv")
    assert r.status_code == 200 and "Urząd Dzielnicy" in r.content.decode("utf-8-sig")


def test_export_is_404_when_empty(client):
    assert client.get("/dane-gov-export.csv").status_code == 404


def test_feed_xml_md5_and_head(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aed.example.org/")
    client.post("/add-aed", json=FORM)
    xml = client.get("/aed-feed.xml")
    assert xml.headers["content-type"].startswith("application/xml")
    assert client.get("/aed-feed.md5").text.strip() == hashlib.md5(xml.content).hexdigest()
    assert client.head("/aed-feed.xml").status_code == 200 and client.head("/aed-feed.md5").status_code == 200
    assert re.search(r"<url>https://aed\.example\.org/dane-gov-export\.csv\?v=[0-9a-f]{12}</url>", xml.text)


def test_feed_url_changes_when_data_changes(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aed.example.org")
    url = lambda: re.search(r"<url>(.*?)</url>", client.get("/aed-feed.xml").text).group(1)
    client.post("/add-aed", json=FORM)
    before = url()
    assert url() == before  # stable while nothing changes
    client.post("/add-aed", json=FORM | {"nazwa_obiektu": "Inny obiekt"})
    assert url() != before  # MCOD sees a new URL and re-downloads the file


def test_large_list_is_gzipped(client):
    import pandas as pd
    df = pd.DataFrame([good_row(f"Obiekt {i}", szerokosc_geo=51 + i / 10000) for i in range(300)])
    main.store_records(df)
    r = client.get("/api/aed-list", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip" and len(r.json()["items"]) == 300


def test_favicon_and_pages(client):
    assert client.get("/favicon.svg").headers["content-type"] == "image/svg+xml"
    assert client.get("/favicon.ico", follow_redirects=False).headers["location"] == "/favicon.svg"
    html = client.get("/").text
    assert 'rel="icon"' in html and "leaflet.css" in html and "markercluster" in html  # map assets must stay linked


def test_reverse_preview_rejects_points_outside_poland(client):
    assert client.get("/reverse-preview", params={"lat": 10, "lon": 10}).json()["found"] is False
