"""Lazy reverse geocoding of AEDs that have no address (most OpenStreetMap points)."""

import threading

import pytest
from fastapi.testclient import TestClient

import aed_db
import main
from conftest import needs_db

pytestmark = needs_db


@pytest.fixture
def client(db, monkeypatch):
    calls = []

    def fake_geocoder(lat, lon):
        calls.append((lat, lon))
        return "ul. Piotrkowska 10, 90-001 Łódź", "90-001"

    monkeypatch.setattr(main, "find_address_details", fake_geocoder)
    monkeypatch.setattr(main, "_address_misses", {})
    with aed_db._connect() as conn:
        conn.execute("INSERT INTO defibrylatory (zrodlo, zrodlo_id, lat, lon, nazwa) VALUES ('osm','node/1',51.76,19.45,'Defibrylator AED')")
        conn.execute("INSERT INTO defibrylatory (zrodlo, zrodlo_id, lat, lon, adres) VALUES ('osm','node/2',51.77,19.46,'Wpisana 5, Łódź')")
    aed_db._invalidate_cache()
    c = TestClient(main.app)
    c.geocoder_calls = calls
    return c


def stored(zrodlo_id):
    with aed_db._connect() as conn:
        return conn.execute("SELECT adres FROM defibrylatory WHERE zrodlo_id = %s", (zrodlo_id,)).fetchone()[0]


def test_missing_address_is_geocoded_returned_and_stored(client):
    r = client.get("/api/aed-address", params={"id": "osm:node/1"})
    assert r.status_code == 200
    assert r.json() == {"found": True, "ulica_i_numer": "ul. Piotrkowska 10", "kod_pocztowy": "90-001", "miasto": "Łódź"}
    assert client.geocoder_calls == [(51.76, 19.45)]
    assert stored("node/1") == "ul. Piotrkowska 10, 90-001 Łódź"
    assert "max-age" in r.headers["cache-control"]  # a found address never changes


def test_second_request_and_other_endpoints_use_the_stored_address_without_geocoding(client):
    client.get("/api/aed-address", params={"id": "osm:node/1"})
    client.get("/api/aed-address", params={"id": "osm:node/1"})
    assert len(client.geocoder_calls) == 1
    nearby = client.get("/api/aed-nearby", params={"lat": 51.76, "lon": 19.45, "limit": 5}).json()["items"]
    first = next(i for i in nearby if i["id_zewnetrzne"] == "osm:node/1")
    assert first["ulica_i_numer"] == "ul. Piotrkowska 10" and first["miasto"] == "Łódź"


def test_address_survives_a_cache_reload(client):
    client.get("/api/aed-address", params={"id": "osm:node/1"})
    aed_db._invalidate_cache()  # as after a restart: read straight from the table
    assert aed_db.find_item("osm:node/1")["ulica_i_numer"] == "ul. Piotrkowska 10"


def test_existing_address_is_returned_and_never_overwritten(client):
    r = client.get("/api/aed-address", params={"id": "osm:node/2"}).json()
    assert r["found"] and r["ulica_i_numer"] == "Wpisana 5"
    assert client.geocoder_calls == []
    assert aed_db.store_address("osm:node/2", "Cudza 1, Łódź") is not None
    assert stored("node/2") == "Wpisana 5, Łódź"  # the guarded UPDATE leaves a person's address alone


def test_unknown_id_is_404_so_the_endpoint_is_not_an_open_geocoder(client):
    assert client.get("/api/aed-address", params={"id": "osm:node/999"}).status_code == 404
    assert client.get("/api/aed-address", params={"id": "node/1"}).status_code == 404
    assert client.get("/api/aed-address").status_code == 422
    assert client.geocoder_calls == []


def test_no_result_is_not_found_and_is_not_retried_immediately(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "find_address_details", lambda lat, lon: calls.append(1) or (None, None))
    for _ in range(3):
        r = client.get("/api/aed-address", params={"id": "osm:node/1"})
        assert r.status_code == 200 and r.json()["found"] is False
    assert len(calls) == 1  # remembered as a miss, so Nominatim is not hammered
    assert stored("node/1") is None


def test_too_many_lookups_at_once_get_429(client, monkeypatch):
    monkeypatch.setattr(main, "ADDRESS_LOOKUP_SLOTS", threading.BoundedSemaphore(1))
    assert main.ADDRESS_LOOKUP_SLOTS.acquire(blocking=False)  # another request is geocoding right now
    r = client.get("/api/aed-address", params={"id": "osm:node/1"})
    assert r.status_code == 429 and r.json()["busy"] is True and r.headers["retry-after"]
    main.ADDRESS_LOOKUP_SLOTS.release()
    assert client.get("/api/aed-address", params={"id": "osm:node/1"}).status_code == 200  # slot is released afterwards


def test_slot_is_released_even_when_the_geocoder_fails(client, monkeypatch):
    monkeypatch.setattr(main, "ADDRESS_LOOKUP_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(main, "find_address_details", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        client.get("/api/aed-address", params={"id": "osm:node/1"})
    assert main.ADDRESS_LOOKUP_SLOTS.acquire(blocking=False)


def test_without_a_database_the_endpoint_is_unavailable(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert TestClient(main.app).get("/api/aed-address", params={"id": "osm:node/1"}).status_code == 404


def test_page_resolves_addresses_through_the_endpoint():
    from pathlib import Path
    js = (Path(main.__file__).parent / "request.js").read_text(encoding="utf-8")
    assert "/api/aed-address" in js and "resolveAddress" in js
