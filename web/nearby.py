"""Nearest-AED lookup over the in-memory item list (the list is cached by aed_db, so no extra DB round trip)."""

import heapq
import math
from typing import Any, Dict, List

EARTH_RADIUS_KM = 6371.0
DEFAULT_LIMIT = 300
MAX_LIMIT = 1000


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle (haversine) distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def nearest(items: List[Dict[str, Any]], lat: float, lon: float, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """The `limit` AEDs closest to (lat, lon), nearest first, each with `odleglosc_km`.

    `radius_km` is the distance to the farthest returned item, i.e. the area around the point that is
    guaranteed to be fully covered; it is None when every stored AED was returned (nothing more to load).
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    scale = math.cos(math.radians(lat))

    def approx(item: Dict[str, Any]) -> float:  # cheap ordering; exact distance is computed for the winners only
        dy = item["szerokosc_geo"] - lat
        dx = (item["dlugosc_geo"] - lon) * scale
        return dx * dx + dy * dy

    picked = []
    for item in heapq.nsmallest(limit, items, key=approx):
        d = distance_km(lat, lon, item["szerokosc_geo"], item["dlugosc_geo"])
        picked.append({**item, "odleglosc_km": round(d, 3)})
    picked.sort(key=lambda i: i["odleglosc_km"])

    return {
        "items": picked,
        "total": len(items),
        "radius_km": picked[-1]["odleglosc_km"] if picked and len(items) > len(picked) else None,
    }
