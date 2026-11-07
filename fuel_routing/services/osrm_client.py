"""
OSRM Routing client for fetching driving routes between two US coordinates.
Uses the free public OSRM driving service.
1 single API call retrieves complete geometry, distance, and duration.
"""
import json
import logging
import urllib.request
from typing import Any, Dict, List, Tuple
from django.core.cache import cache

logger = logging.getLogger(__name__)

OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/driving"
METERS_TO_MILES = 0.000621371
ROUTE_CACHE_TTL_SECONDS = 86400  # 24 hours

def get_driving_route(
    start_lat: float,
    start_lon: float,
    finish_lat: float,
    finish_lon: float
) -> Dict[str, Any]:
    """
    Calls OSRM driving API.
    Caches successful route responses to minimize external calls.
    """
    cache_key = f"osrm_route_{start_lat:.4f}_{start_lon:.4f}_{finish_lat:.4f}_{finish_lon:.4f}"
    cached = cache.get(cache_key)
    if cached is not None:
        cached_copy = dict(cached)
        cached_copy["cached"] = True
        return cached_copy

    # Note: OSRM expects coordinates as lon,lat;lon,lat
    url = f"{OSRM_BASE_URL}/{start_lon:.6f},{start_lat:.6f};{finish_lon:.6f},{finish_lat:.6f}?overview=full&geometries=geojson"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SpotterFuelRouting/1.0"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.load(resp)
    except Exception as e:
        logger.error("Failed to connect to OSRM API: %s", e)
        raise RuntimeError(f"Routing service unavailable: {str(e)}")

    if data.get("code") != "Ok" or not data.get("routes"):
        msg = data.get("message", "No route found between specified points.")
        raise ValueError(f"OSRM Routing failed: {msg}")

    route = data["routes"][0]
    distance_meters = float(route["distance"])
    duration_seconds = float(route["duration"])
    distance_miles = round(distance_meters * METERS_TO_MILES, 2)
    duration_hours = round(duration_seconds / 3600.0, 2)

    # OSRM geojson coordinates are [lon, lat]
    lon_lat_coords = route["geometry"]["coordinates"]
    lat_lon_coords: List[List[float]] = [[pt[1], pt[0]] for pt in lon_lat_coords]

    # Calculate bounding box
    lats = [pt[0] for pt in lat_lon_coords]
    lons = [pt[1] for pt in lat_lon_coords]
    bounds = [min(lats), min(lons), max(lats), max(lons)]

    result = {
        "distance_miles": distance_miles,
        "duration_seconds": duration_seconds,
        "duration_hours": duration_hours,
        "coordinates": lat_lon_coords,
        "geojson_geometry": route["geometry"],
        "bounds": bounds,
        "cached": False,
    }

    try:
        cache.set(cache_key, result, timeout=ROUTE_CACHE_TTL_SECONDS)
    except Exception as e:
        logger.warning("Failed to store route in cache: %s", e)

    return result

