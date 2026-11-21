import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

GEOCODER_URL    = os.environ.get("GEOCODER_URL", "https://nominatim.openstreetmap.org").rstrip("/")
USER_AGENT      = os.environ.get("GEOCODER_USER_AGENT", "defibrylokator/0.1")
MIN_INTERVAL    = float(os.environ.get("GEOCODER_MIN_INTERVAL", "1.0"))
TIMEOUT         = float(os.environ.get("GEOCODER_TIMEOUT", "5"))

LAT_RANGE = (49.0, 55.0)
LON_RANGE = (14.1, 24.2)

_CACHE_DECIMALS = 5  # ok. 1 m
_cache: dict[tuple[float, float], str] = {}
_forward_cache: dict[str, tuple[float, float]] = {}
_cache_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_request = 0.0


def _throttle() -> None:
    global _last_request
    if MIN_INTERVAL <= 0:
        return
    with _rate_lock:
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _fetch(path: str, params: dict):
    query = urllib.parse.urlencode(
        {**params, "format": "jsonv2", "addressdetails": 1, "accept-language": "pl"}
    )
    request = urllib.request.Request(
        f"{GEOCODER_URL}/{path}?{query}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    _throttle()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def format_address(address: dict) -> Optional[str]:
    """Składa adres z pól `address` odpowiedzi Nominatim: 'Ulica 1, 00-001 Miasto'."""
    locality = next(
        (address[k] for k in ("city", "town", "village", "municipality", "hamlet") if address.get(k)), None
    )
    street = next(
        (address[k] for k in ("road", "pedestrian", "footway", "square", "hamlet", "village", "suburb") if address.get(k)),
        None,
    )
    if street is None:
        return None
    if address.get("house_number"):
        street = f"{street} {address['house_number']}"
    city = " ".join(p for p in (address.get("postcode"), locality) if p)
    # Wieś bez ulic: 'Kraszew 12, Kraszew' -> 'Kraszew 12'
    if locality and street.split(" ")[0] == locality and not address.get("postcode"):
        city = ""
    return ", ".join(p for p in (street, city) if p)


def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    """Zwraca adres dla współrzędnych albo None (poza Polską, brak wyniku, błąd sieci)."""
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        return None
    key = (round(lat, _CACHE_DECIMALS), round(lon, _CACHE_DECIMALS))
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    data = _fetch("reverse", {"lat": lat, "lon": lon, "zoom": 18})
    if not data or data.get("error"):
        return None
    address = data.get("address") or {}
    if address.get("country_code", "pl") != "pl":
        return None
    result = format_address(address)
    if result: # olewamy bledne
        with _cache_lock:
            _cache[key] = result
    return result


def forward_geocode(adres: str, min_place_rank: int = 26) -> Optional[tuple[float, float]]:
    """Zwraca (lat, lon) dla adresu albo None (brak wyniku, wynik poza Polską, błąd sieci).

    Wyniki mniej dokładne niż `min_place_rank` są odrzucane: 26 = ulica, 30 = budynek/numer,
    a samo miasto (16) dałoby punkt w centrum miejscowości, co dla AED byłoby mylące.
    """
    adres, error = check_address(adres)
    if error or not adres:
        return None
    key = adres.casefold()
    with _cache_lock:
        if key in _forward_cache:
            return _forward_cache[key]

    data = _fetch("search", {"q": adres, "countrycodes": "pl", "limit": 1})
    if not isinstance(data, list) or not data:
        return None
    hit = data[0]
    try:
        lat, lon = float(hit["lat"]), float(hit["lon"])
        rank = int(hit.get("place_rank", 0))
    except (KeyError, TypeError, ValueError):
        return None
    if rank < min_place_rank:
        return None
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        return None
    with _cache_lock:
        _forward_cache[key] = (lat, lon)
    return lat, lon


_POSTCODE_LIKE = re.compile(r"(?<!\d)\d{2}\s*[-–]?\s*\d{3}(?!\d)")


def check_address(adres: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Walidacja adresu po stronie serwera. Zwraca (znormalizowany adres, komunikat błędu).

    Puste wejście jest poprawne (adres jest opcjonalny). Kod pocztowy, jeśli występuje,
    jest sprowadzany do postaci NN-NNN
    """
    if adres is None or not adres.strip():
        return None, None
    adres = re.sub(r"\s+", " ", adres.strip())
    if len(adres) < 3:
        return None, "Adres jest za krótki (min. 3 znaki)!"
    if len(adres) > 200:
        return None, "Adres jest za długi (maks. 200 znaków)!"
    if re.search(r"[<>\x00-\x1f]", adres):
        return None, "Adres zawiera niedozwolone znaki!"
    adres = _POSTCODE_LIKE.sub(lambda m: re.sub(r"\D", "", m.group())[:2] + "-" + re.sub(r"\D", "", m.group())[2:], adres)
    return adres, None
