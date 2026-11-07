"""
Geocoding service for resolving US locations to latitude/longitude coordinates.
Features:
1. Direct coordinate parsing (e.g. "40.7128,-74.0060").
2. Instant offline resolution for 40,000+ US cities/places via local index (0 API calls).
3. Online fallback via Photon / Nominatim if not found locally (max 1 call per query).
"""
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CITIES_LOOKUP_PATH = os.path.join(BASE_DIR, "data", "cities_lookup.json")

# In-memory cache for loaded cities lookup
_LOCAL_CITIES: Optional[dict] = None
_ONLINE_CACHE: dict = {}

US_STATE_CODES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC"
}

def _get_local_cities() -> dict:
    global _LOCAL_CITIES
    if _LOCAL_CITIES is None:
        if os.path.exists(CITIES_LOOKUP_PATH):
            with open(CITIES_LOOKUP_PATH, "r", encoding="utf-8") as f:
                _LOCAL_CITIES = json.load(f)
        else:
            logger.warning("Cities lookup file not found at %s", CITIES_LOOKUP_PATH)
            _LOCAL_CITIES = {}
    return _LOCAL_CITIES

def _clean_str(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())

def parse_coordinates(query: str) -> Optional[Tuple[float, float, str]]:
    """
    Checks if the string is formatted as lat,lon or lon,lat numbers.
    Returns (lat, lon, formatted_name) or None.
    """
    coord_pattern = r"^[-+]?([1-8]?\d(\.\d+)?|90(\.0+)?),\s*[-+]?(180(\.0+)?|((1[0-7]\d)|([1-9]?\d))(\.\d+)?)$"
    m = re.match(coord_pattern, query.strip())
    if m:
        parts = [float(p.strip()) for p in query.split(",")]
        # In the US, latitude is ~24 to ~50, longitude is ~ -125 to ~ -66
        # If first number is negative and second positive, user might have entered lon,lat
        if parts[0] < 0 and parts[1] > 0:
            lon, lat = parts[0], parts[1]
        else:
            lat, lon = parts[0], parts[1]
        return lat, lon, f"{lat:.4f}, {lon:.4f}"
    return None

def geocode_location(query: str) -> Tuple[float, float, str]:
    """
    Geocodes a location query within the USA.
    Returns (latitude, longitude, formatted_label).
    Raises ValueError if location cannot be resolved.
    """
    query = query.strip()
    if not query:
        raise ValueError("Location query cannot be empty.")

    # 1. Direct coordinates
    direct = parse_coordinates(query)
    if direct:
        return direct

    # 2. Check local offline lookup
    local_cities = _get_local_cities()
    # Normalize query: could be "New York, NY" or "Austin, Texas"
    parts = [p.strip() for p in query.split(",") if p.strip()]
    if len(parts) >= 2:
        city_raw = parts[0]
        state_raw = parts[1].upper()
        # Handle full state name like "Texas"
        if state_raw in US_STATE_CODES:
            state_code = US_STATE_CODES[state_raw]
        else:
            state_code = state_raw[:2]
        
        city_norm = _clean_str(city_raw)
        key = f"{city_norm}_{state_code}"
        if key in local_cities:
            coords = local_cities[key]
            return float(coords[0]), float(coords[1]), f"{city_raw.title()}, {state_code}"

    # Also check if whole string exists in local lookup
    norm_full = _clean_str(query)
    for k, coords in local_cities.items():
        if k.replace("_", "") == norm_full:
            parts = k.split("_")
            return float(coords[0]), float(coords[1]), f"{parts[0].title()}, {parts[1]}"

    # 3. Check memory cache for previous online lookups
    cache_key = query.lower()
    if cache_key in _ONLINE_CACHE:
        return _ONLINE_CACHE[cache_key]

    # 4. Fallback: Photon Komoot API (free OSM geocoding, high speed, no key needed)
    try:
        encoded_query = urllib.parse.quote(f"{query}, United States")
        url = f"https://photon.komoot.io/api/?q={encoded_query}&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "SpotterFuelRouting/1.0"})
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.load(resp)
            features = data.get("features", [])
            if features:
                geom = features[0].get("geometry", {})
                coords = geom.get("coordinates", [])
                if len(coords) >= 2:
                    lon, lat = coords[0], coords[1]
                    props = features[0].get("properties", {})
                    name = props.get("name", query)
                    state = props.get("state", "")
                    label = f"{name}, {state}" if state else name
                    res = (float(lat), float(lon), label)
                    _ONLINE_CACHE[cache_key] = res
                    return res
    except Exception as e:
        logger.warning("Photon geocoding fallback failed: %s", e)

    raise ValueError(f"Could not resolve location '{query}'. Please provide a valid US city/state (e.g. 'Chicago, IL') or coordinates.")

