"""
Views for Fuel Routing API and interactive Map UI.
"""
import time
from typing import Any, Dict, List
import urllib.parse
from django.shortcuts import render
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fuel_routing.serializers import RouteRequestSerializer
from fuel_routing.services.geocoding import geocode_location
from fuel_routing.services.osrm_client import get_driving_route
from fuel_routing.services.spatial_index import (
    find_candidate_stations_along_route,
    get_spatial_index,
)
from fuel_routing.services.optimizer import optimize_fuel_stops


def _format_duration(hours: float) -> str:
    h = int(hours)
    m = int(round((hours - h) * 60))
    if h > 0:
        return f"{h} hr {m} min"
    return f"{m} min"


def _build_geojson(
    route_geometry: Dict[str, Any],
    start_info: Dict[str, Any],
    finish_info: Dict[str, Any],
    stops: List[Dict[str, Any]]
) -> Dict[str, Any]:
    features = [
        {
            "type": "Feature",
            "geometry": route_geometry,
            "properties": {
                "name": "Route Path",
                "stroke": "#2563eb",
                "stroke-width": 5,
                "stroke-opacity": 0.85
            }
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [start_info["longitude"], start_info["latitude"]]
            },
            "properties": {
                "type": "start",
                "title": f"Start: {start_info['label']}",
                "description": f"Starting coordinate: {start_info['latitude']:.4f}, {start_info['longitude']:.4f}"
            }
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [finish_info["longitude"], finish_info["latitude"]]
            },
            "properties": {
                "type": "finish",
                "title": f"Finish: {finish_info['label']}",
                "description": f"Destination coordinate: {finish_info['latitude']:.4f}, {finish_info['longitude']:.4f}"
            }
        }
    ]

    for s in stops:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [s["coordinates"]["longitude"], s["coordinates"]["latitude"]]
            },
            "properties": {
                "type": "fuel_stop",
                "stop_number": s["stop_index"],
                "name": s["truckstop_name"],
                "address": s["address"],
                "city": s["city"],
                "state": s["state"],
                "retail_price": s["retail_price"],
                "gallons_pumped": s["fuel_pumped_gallons"],
                "cost_dollars": s["cost_dollars"],
                "mile_marker": s["distance_along_route_miles"]
            }
        })

    return {
        "type": "FeatureCollection",
        "features": features
    }


class RouteFuelAPIView(APIView):
    """
    API endpoint that accepts Start and Finish locations in the USA.
    Returns:
    - Route driving distance, duration, and bounds
    - Optimal fuel stops along the route (minimizing fuel costs)
    - Total fuel consumed, purchased, and total money spent ($)
    - Complete GeoJSON feature collection for mapping
    - Execution metrics (response time, API call counts)
    """

    def get(self, request, *args, **kwargs):
        serializer = RouteRequestSerializer(data=request.query_params)
        if not serializer.is_valid():
            return Response({"status": "error", "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        return self._process_request(serializer.validated_data)

    def post(self, request, *args, **kwargs):
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"status": "error", "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        return self._process_request(serializer.validated_data)

    def _process_request(self, data: Dict[str, Any]) -> Response:
        start_query = data["start"]
        finish_query = data["finish"]
        mpg = data.get("mpg", 10.0)
        max_range = data.get("max_range", 500.0)
        buffer_miles = data.get("buffer_miles", 10.0)
        initial_fuel = data.get("initial_fuel_miles", 500.0)
        policy = data.get("fuel_policy", "standard")
        ensure_full_end = (policy == "replace_consumed")

        t_start = time.time()
        api_call_count = 0

        # 1. Geocode Start and Finish
        t0_geo = time.time()
        try:
            s_lat, s_lon, s_label = geocode_location(start_query)
            f_lat, f_lon, f_label = geocode_location(finish_query)
        except ValueError as e:
            return Response({"status": "error", "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        t_geo_ms = round((time.time() - t0_geo) * 1000, 2)

        # 2. Driving Route via OSRM (1 external API call if not cached)
        t0_route = time.time()
        try:
            route_data = get_driving_route(s_lat, s_lon, f_lat, f_lon)
            if not route_data.get("cached", False):
                api_call_count += 1
        except Exception as e:
            return Response(
                {"status": "error", "message": f"Routing service error: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY
            )
        t_route_ms = round((time.time() - t0_route) * 1000, 2)

        dist_miles = route_data["distance_miles"]
        dur_hours = route_data["duration_hours"]

        # 3. Spatial Search: Find candidate stations in corridor
        t0_spatial = time.time()
        cands = find_candidate_stations_along_route(
            route_coords=route_data["coordinates"],
            total_distance_miles=dist_miles,
            buffer_miles=buffer_miles
        )
        t_spatial_ms = round((time.time() - t0_spatial) * 1000, 2)

        # 4. Refueling Optimization
        t0_opt = time.time()
        opt = optimize_fuel_stops(
            total_distance_miles=dist_miles,
            candidate_stations=cands,
            max_range_miles=max_range,
            mpg=mpg,
            initial_fuel_miles=initial_fuel,
            ensure_end_full=ensure_full_end
        )
        t_opt_ms = round((time.time() - t0_opt) * 1000, 2)

        if not opt.get("success"):
            return Response({
                "status": "error",
                "message": opt.get("error", "Failed to optimize fuel stops along route.")
            }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        total_time_ms = round((time.time() - t_start) * 1000, 2)

        start_info = {"query": start_query, "label": s_label, "latitude": s_lat, "longitude": s_lon}
        finish_info = {"query": finish_query, "label": f_label, "latitude": f_lat, "longitude": f_lon}

        geojson = _build_geojson(
            route_geometry=route_data["geojson_geometry"],
            start_info=start_info,
            finish_info=finish_info,
            stops=opt["fuel_stops"]
        )

        total_spent = opt["total_fuel_cost_dollars"]
        total_purchased = opt["total_fuel_purchased_gallons"]
        avg_price = round(total_spent / total_purchased, 3) if total_purchased > 0 else 0.0

        map_query_url = f"/map/?start={urllib.parse.quote_plus(start_query)}&finish={urllib.parse.quote_plus(finish_query)}"

        return Response({
            "status": "success",
            "route": {
                "start": start_info,
                "finish": finish_info,
                "distance_miles": dist_miles,
                "duration_hours": dur_hours,
                "duration_formatted": _format_duration(dur_hours),
                "bounds": route_data["bounds"]
            },
            "fuel_summary": {
                "vehicle_mpg": mpg,
                "max_range_miles": max_range,
                "tank_capacity_gallons": round(max_range / mpg, 2),
                "initial_fuel_gallons": opt["initial_fuel_gallons"],
                "ending_fuel_gallons": opt["ending_fuel_gallons"],
                "total_fuel_consumed_gallons": opt["total_fuel_consumed_gallons"],
                "total_fuel_purchased_gallons": total_purchased,
                "total_money_spent_dollars": total_spent,
                "average_fuel_cost_per_gallon": avg_price,
                "total_refueling_stops": opt["total_stops"]
            },
            "fuel_stops": opt["fuel_stops"],
            "map_url": map_query_url,
            "geojson": geojson,
            "execution_metrics": {
                "external_api_calls": api_call_count,
                "geocoding_time_ms": t_geo_ms,
                "routing_time_ms": t_route_ms,
                "spatial_search_time_ms": t_spatial_ms,
                "optimization_time_ms": t_opt_ms,
                "total_response_time_ms": total_time_ms
            }
        })


class HealthView(APIView):
    def get(self, request, *args, **kwargs):
        stations, _ = get_spatial_index()
        return Response({
            "status": "healthy",
            "service": "Spotter Fuel Routing API",
            "version": "1.0.0",
            "indexed_stations": len(stations),
            "max_vehicle_range_miles": 500,
            "default_mpg": 10
        })


def map_view(request):
    """
    Renders an interactive web interface powered by Leaflet.js
    """
    start_q = request.GET.get("start", "New York, NY")
    finish_q = request.GET.get("finish", "Atlanta, GA")
    return render(request, "map.html", {
        "start": start_q,
        "finish": finish_q,
    })
