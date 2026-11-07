"""
Spatial index and corridor search service for fuel stations along a route polyline.
Uses 3D spherical cKDTree for sub-millisecond proximity queries.
"""
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from scipy.spatial import cKDTree

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIONS_PATH = os.path.join(BASE_DIR, "data", "stations.json")
EARTH_RADIUS_MILES = 3958.8

_STATIONS_DATA: Optional[List[Dict[str, Any]]] = None
_STATIONS_KDTREE: Optional[cKDTree] = None
_STATIONS_CART: Optional[np.ndarray] = None

def _latlon_to_cartesian(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    lat_r = np.radians(lats)
    lon_r = np.radians(lons)
    x = np.cos(lat_r) * np.cos(lon_r)
    y = np.cos(lat_r) * np.sin(lon_r)
    z = np.sin(lat_r)
    return np.column_stack([x, y, z])

def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0)**2
    return 2.0 * EARTH_RADIUS_MILES * math.asin(math.sqrt(max(0.0, min(1.0, a))))

def get_spatial_index() -> Tuple[List[Dict[str, Any]], cKDTree]:
    """
    Initializes and returns cached stations data and cKDTree.
    """
    global _STATIONS_DATA, _STATIONS_KDTREE, _STATIONS_CART
    if _STATIONS_DATA is None or _STATIONS_KDTREE is None:
        if not os.path.exists(STATIONS_PATH):
            raise FileNotFoundError(f"Stations file not found at {STATIONS_PATH}. Run build_station_db.py first.")
        with open(STATIONS_PATH, "r", encoding="utf-8") as f:
            _STATIONS_DATA = json.load(f)

        lats = np.array([s["lat"] for s in _STATIONS_DATA], dtype=np.float64)
        lons = np.array([s["lon"] for s in _STATIONS_DATA], dtype=np.float64)
        _STATIONS_CART = _latlon_to_cartesian(lats, lons)
        _STATIONS_KDTREE = cKDTree(_STATIONS_CART)

    return _STATIONS_DATA, _STATIONS_KDTREE

def find_candidate_stations_along_route(
    route_coords: List[List[float]],
    total_distance_miles: float,
    buffer_miles: float = 10.0,
    sample_interval_miles: float = 5.0
) -> List[Dict[str, Any]]:
    """
    Finds all truckstops within `buffer_miles` of the route polyline.
    Returns sorted list of stations with 'mile' (position along route) and 'detour_miles'.
    """
    stations, stations_tree = get_spatial_index()

    # 1. Compute cumulative distance along the polyline
    n_pts = len(route_coords)
    if n_pts < 2:
        return []

    route_lats = np.array([pt[0] for pt in route_coords], dtype=np.float64)
    route_lons = np.array([pt[1] for pt in route_coords], dtype=np.float64)

    cum_dist = np.zeros(n_pts, dtype=np.float64)
    for i in range(1, n_pts):
        d = _haversine_miles(route_lats[i-1], route_lons[i-1], route_lats[i], route_lons[i])
        cum_dist[i] = cum_dist[i-1] + d

    # Scale cum_dist slightly to match total_distance_miles from OSRM if minor haversine divergence
    if cum_dist[-1] > 0 and total_distance_miles > 0:
        cum_dist = cum_dist * (total_distance_miles / cum_dist[-1])

    # 2. Sample route checkpoints every ~sample_interval_miles to query station KDTree
    sample_indices = [0]
    last_d = 0.0
    for idx, d in enumerate(cum_dist):
        if d - last_d >= sample_interval_miles:
            sample_indices.append(idx)
            last_d = d
    if sample_indices[-1] != n_pts - 1:
        sample_indices.append(n_pts - 1)

    sample_cart = _latlon_to_cartesian(route_lats[sample_indices], route_lons[sample_indices])

    # Chord radius on unit sphere for buffer_miles
    theta = buffer_miles / EARTH_RADIUS_MILES
    chord_radius = 2.0 * math.sin(theta / 2.0)

    # Query station KDTree for candidates near sample checkpoints
    nearby_station_indices = set()
    matches = stations_tree.query_ball_point(sample_cart, r=chord_radius)
    for idx_list in matches:
        nearby_station_indices.update(idx_list)

    if not nearby_station_indices:
        return []

    # 3. Build a KD-Tree of the route points to find exact closest point and mile for each candidate
    route_cart = _latlon_to_cartesian(route_lats, route_lons)
    route_tree = cKDTree(route_cart)

    candidate_list = []
    cand_indices = list(nearby_station_indices)
    cand_cart = _STATIONS_CART[cand_indices]

    # Query route_tree for closest vertex to each candidate station
    _, nearest_vertex_indices = route_tree.query(cand_cart)

    for i, s_idx in enumerate(cand_indices):
        st = stations[s_idx]
        v_idx = nearest_vertex_indices[i]

        # Calculate exact haversine distance from station to nearest vertex
        detour = _haversine_miles(st["lat"], st["lon"], route_lats[v_idx], route_lons[v_idx])
        if detour <= buffer_miles:
            mile_pos = round(float(cum_dist[v_idx]), 2)
            # Only consider stations along the route between 0 and total_distance_miles
            if 0.0 <= mile_pos <= total_distance_miles:
                cand = dict(st)
                cand["mile"] = mile_pos
                cand["detour_miles"] = round(detour, 2)
                candidate_list.append(cand)

    # Sort ascending by mile marker along the route
    candidate_list.sort(key=lambda x: (x["mile"], x["price"]))
    return candidate_list

