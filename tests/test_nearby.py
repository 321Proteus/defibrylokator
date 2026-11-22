import math
import random

import pytest
from fastapi.testclient import TestClient

import main
import nearby
from conftest import good_row


def item(name, lat, lon):
    return {"nazwa_obiektu": name, "szerokosc_geo": lat, "dlugosc_geo": lon, "id_zewnetrzne": name}


WARSAW = (52.2297, 21.0122)
CITIES = [item("Warszawa", *WARSAW), item("Łódź", 51.7592, 19.4560), item("Gdańsk", 54.3520, 18.6466),
          item("Kraków", 50.0647, 19.9450), item("Poznań", 52.4064, 16.9252)]


def test_distance_known_values():
    assert nearby.distance_km(*WARSAW, *WARSAW) == 0
    assert nearby.distance_km(52.2297, 21.0122, 51.7592, 19.4560) == pytest.approx(117.5, abs=1.5)  # Warszawa-Łódź
    assert nearby.distance_km(0, 0, 0, 1) == pytest.approx(111.19, abs=0.1)


def test_nearest_orders_by_distance_and_limits():
    result = nearby.nearest(CITIES, *WARSAW, limit=3)
    assert [i["nazwa_obiektu"] for i in result["items"]] == ["Warszawa", "Łódź", "Kraków"]  # Kraków ~252 km, Poznań ~280 km
    assert result["items"][0]["odleglosc_km"] == 0 and result["total"] == 5
    assert result["radius_km"] == result["items"][-1]["odleglosc_km"]


def test_radius_is_none_when_everything_was_returned():
    assert nearby.nearest(CITIES, *WARSAW, limit=5)["radius_km"] is None
    assert nearby.nearest(CITIES, *WARSAW, limit=500)["radius_km"] is None


def test_empty_list_and_limit_clamping():
    assert nearby.nearest([], 52.0, 19.0) == {"items": [], "total": 0, "radius_km": None}
    many = [item(str(i), 52 + i / 10000, 19) for i in range(nearby.MAX_LIMIT + 50)]
    assert len(nearby.nearest(many, 52.0, 19.0, limit=10 ** 6)["items"]) == nearby.MAX_LIMIT
    assert len(nearby.nearest(many, 52.0, 19.0, limit=0)["items"]) == 1


def test_matches_brute_force_haversine():
    rnd = random.Random(7)
    points = [item(str(i), rnd.uniform(49, 54.8), rnd.uniform(14.2, 24.1)) for i in range(2000)]
    lat, lon = 52.4, 19.0
    expected = sorted(points, key=lambda p: nearby.distance_km(lat, lon, p["szerokosc_geo"], p["dlugosc_geo"]))[:50]
    got = nearby.nearest(points, lat, lon, limit=50)["items"]
    assert [p["nazwa_obiektu"] for p in got] == [p["nazwa_obiektu"] for p in expected]


def test_longitude_degrees_shrink_with_latitude():
    # at 54°N one degree of longitude is ~65 km, one degree of latitude ~111 km: the east neighbour is closer
    points = [item("east", 54.0, 19.9), item("north", 54.9, 19.0)]
    assert nearby.nearest(points, 54.0, 19.0, limit=1)["items"][0]["nazwa_obiektu"] == "east"


# ---------- endpoint ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MASTER_CSV_PATH", tmp_path / "master.csv")
    import pandas as pd
    rows = [good_row(f"Obiekt {i:03d}", szerokosc_geo=51.0 + i * 0.01, dlugosc_geo=19.0) for i in range(120)]
    main.store_records(pd.DataFrame(rows))
    return TestClient(main.app)


def test_endpoint_returns_only_the_requested_nearest_points(client):
    r = client.get("/api/aed-nearby", params={"lat": 51.0, "lon": 19.0, "limit": 10})
    body = r.json()
    assert r.status_code == 200 and len(body["items"]) == 10 and body["total"] == 120
    assert [i["nazwa_obiektu"] for i in body["items"]][:3] == ["Obiekt 000", "Obiekt 001", "Obiekt 002"]
    assert body["radius_km"] == pytest.approx(body["items"][-1]["odleglosc_km"])
    assert "max-age" in r.headers["cache-control"]


def test_endpoint_default_limit_and_far_point(client):
    far = client.get("/api/aed-nearby", params={"lat": 54.5, "lon": 23.9}).json()  # NE Poland
    assert len(far["items"]) == 120 and far["radius_km"] is None  # fewer rows than the limit: all returned
    assert far["items"][0]["nazwa_obiektu"] == "Obiekt 119"  # the northernmost point is the closest


@pytest.mark.parametrize("params", [
    {}, {"lat": 52}, {"lat": "x", "lon": 19}, {"lat": 91, "lon": 19}, {"lat": 52, "lon": 181},
    {"lat": 52, "lon": 19, "limit": 0}, {"lat": 52, "lon": 19, "limit": nearby.MAX_LIMIT + 1},
])
def test_endpoint_rejects_bad_parameters(client, params):
    assert client.get("/api/aed-nearby", params=params).status_code == 422


def test_endpoint_items_keep_the_fields_the_map_needs(client):
    (first,) = client.get("/api/aed-nearby", params={"lat": 51.0, "lon": 19.0, "limit": 1}).json()["items"]
    for key in ("id_zewnetrzne", "nazwa_obiektu", "miasto", "ulica_i_numer", "opis_lokalizacji", "szerokosc_geo",
                "dlugosc_geo", "dostepnosc", "status", "odleglosc_km"):
        assert key in first


def test_map_page_uses_nearby_endpoint_not_full_list():
    from pathlib import Path
    js = (Path(main.__file__).parent / "request.js").read_text(encoding="utf-8")
    assert "/api/aed-nearby" in js and "/api/aed-list" not in js
