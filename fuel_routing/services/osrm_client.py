"""
Routing client for fetching driving routes between two US coordinates.
Supports:
1. OpenRouteService (when ORS_API_KEY is configured).
2. Project OSRM (free public routing service fallback).
3. 24-hour in-memory route caching.
"""
import json
import logging
import urllib.request
from typing import Any, Dict, List, Tuple
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/driving"
ORS_BASE_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
METERS_TO_MILES = 0.000621371
ROUTE_CACHE_TTL_SECONDS = 86400  # 24 hours


def _fetch_from_ors(
    start_lat: float,
    start_lon: float,
    finish_lat: float,
    finish_lon: float,
    api_key: str
) -> Dict[str, Any]:
    """
    Fetches route from OpenRouteService API.
    """
    body = json.dumps({
        "coordinates": [[start_lon, start_lat], [finish_lon, finish_lat]],
        "instructions": False,
        "preference": "recommended"
    }).encode("utf-8")

    req = urllib.request.Request(
        ORS_BASE_URL,
        data=body,
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
            "User-Agent": "SpotterFuelRouting/1.0"
        }
    )

    with urllib.request.urlopen(req, timeout=12.0) as resp:
        data = json.load(resp)

    features = data.get("features", [])
    if not features:
        raise ValueError("No route found in OpenRouteService response.")

    feat = features[0]
    summary = feat.get("properties", {}).get("summary", {})
    distance_meters = float(summary.get("distance", 0.0))
    duration_seconds = float(summary.get("duration", 0.0))

    distance_miles = round(distance_meters * METERS_TO_MILES, 2)
    duration_hours = round(duration_seconds / 3600.0, 2)

    # GeoJSON coordinates are [lon, lat]
    lon_lat_coords = feat.get("geometry", {}).get("coordinates", [])
    lat_lon_coords: List[List[float]] = [[pt[1], pt[0]] for pt in lon_lat_coords]

    # Bounding box
    bbox = feat.get("bbox") or data.get("bbox")
    if bbox and len(bbox) >= 4:
        # bbox is [min_lon, min_lat, max_lon, max_lat]
        bounds = [bbox[1], bbox[0], bbox[3], bbox[2]]
    else:
        lats = [pt[0] for pt in lat_lon_coords]
        lons = [pt[1] for pt in lat_lon_coords]
        bounds = [min(lats), min(lons), max(lats), max(lons)]

    return {
        "provider": "OpenRouteService",
        "distance_miles": distance_miles,
        "duration_seconds": duration_seconds,
        "duration_hours": duration_hours,
        "coordinates": lat_lon_coords,
        "geojson_geometry": feat.get("geometry", {}),
        "bounds": bounds,
        "cached": False,
    }


def _fetch_from_osrm(
    start_lat: float,
    start_lon: float,
    finish_lat: float,
    finish_lon: float
) -> Dict[str, Any]:
    """
    Fetches route from free Project OSRM API.
    """
    url = f"{OSRM_BASE_URL}/{start_lon:.6f},{start_lat:.6f};{finish_lon:.6f},{finish_lat:.6f}?overview=full&geometries=geojson"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SpotterFuelRouting/1.0"}
    )

    with urllib.request.urlopen(req, timeout=12.0) as resp:
        data = json.load(resp)

    if data.get("code") != "Ok" or not data.get("routes"):
        msg = data.get("message", "No route found between specified points.")
        raise ValueError(f"OSRM Routing failed: {msg}")

    route = data["routes"][0]
    distance_meters = float(route["distance"])
    duration_seconds = float(route["duration"])
    distance_miles = round(distance_meters * METERS_TO_MILES, 2)
    duration_hours = round(duration_seconds / 3600.0, 2)

    lon_lat_coords = route["geometry"]["coordinates"]
    lat_lon_coords: List[List[float]] = [[pt[1], pt[0]] for pt in lon_lat_coords]

    lats = [pt[0] for pt in lat_lon_coords]
    lons = [pt[1] for pt in lat_lon_coords]
    bounds = [min(lats), min(lons), max(lats), max(lons)]

    return {
        "provider": "OSRM",
        "distance_miles": distance_miles,
        "duration_seconds": duration_seconds,
        "duration_hours": duration_hours,
        "coordinates": lat_lon_coords,
        "geojson_geometry": route["geometry"],
        "bounds": bounds,
        "cached": False,
    }


def get_driving_route(
    start_lat: float,
    start_lon: float,
    finish_lat: float,
    finish_lon: float
) -> Dict[str, Any]:
    """
    Retrieves driving route between two points.
    Checks memory cache first.
    Attempts OpenRouteService if API key is present, falls back to OSRM.
    """
    cache_key = f"driving_route_{start_lat:.4f}_{start_lon:.4f}_{finish_lat:.4f}_{finish_lon:.4f}"
    cached = cache.get(cache_key)
    if cached is not None:
        cached_copy = dict(cached)
        cached_copy["cached"] = True
        return cached_copy

    ors_key = getattr(settings, "ORS_API_KEY", "").strip()

    result = None

    # 1. Try OpenRouteService if key is available
    if ors_key:
        try:
            result = _fetch_from_ors(start_lat, start_lon, finish_lat, finish_lon, ors_key)
            logger.info("Successfully fetched route via OpenRouteService")
        except Exception as e:
            logger.warning("OpenRouteService failed, falling back to OSRM: %s", e)

    # 2. Fallback to OSRM
    if result is None:
        try:
            result = _fetch_from_osrm(start_lat, start_lon, finish_lat, finish_lon)
            logger.info("Successfully fetched route via OSRM")
        except Exception as e:
            logger.error("OSRM routing also failed: %s", e)
            raise RuntimeError(f"All routing services failed: {str(e)}")

    try:
        cache.set(cache_key, result, timeout=ROUTE_CACHE_TTL_SECONDS)
    except Exception as e:
        logger.warning("Failed to store route in cache: %s", e)

    return result
