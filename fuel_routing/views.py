"""
Views for Fuel Routing API and interactive Map UI.
"""
import time
import logging
from typing import Any, Dict, List
import urllib.parse
from django.shortcuts import render
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

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


class LocationSearchAPIView(APIView):
    """
    Search US and international cities for autocomplete dropdowns.
    Sub-millisecond in-memory lookup over 40,000+ US cities plus global hubs.
    """
    GLOBAL_HUBS = [
        # India (All 28 States & UTs)
        {"city": "Mumbai", "state": "Maharashtra", "country": "India", "country_code": "IN", "lat": 19.0760, "lon": 72.8777},
        {"city": "Delhi", "state": "Delhi", "country": "India", "country_code": "IN", "lat": 28.6139, "lon": 77.2090},
        {"city": "Bengaluru", "state": "Karnataka", "country": "India", "country_code": "IN", "lat": 12.9716, "lon": 77.5946},
        {"city": "Hyderabad", "state": "Telangana", "country": "India", "country_code": "IN", "lat": 17.3850, "lon": 78.4867},
        {"city": "Ahmedabad", "state": "Gujarat", "country": "India", "country_code": "IN", "lat": 23.0225, "lon": 72.5714},
        {"city": "Chennai", "state": "Tamil Nadu", "country": "India", "country_code": "IN", "lat": 13.0827, "lon": 80.2707},
        {"city": "Kolkata", "state": "West Bengal", "country": "India", "country_code": "IN", "lat": 22.5726, "lon": 88.3639},
        {"city": "Surat", "state": "Gujarat", "country": "India", "country_code": "IN", "lat": 21.1702, "lon": 72.8311},
        {"city": "Pune", "state": "Maharashtra", "country": "India", "country_code": "IN", "lat": 18.5204, "lon": 73.8567},
        {"city": "Jaipur", "state": "Rajasthan", "country": "India", "country_code": "IN", "lat": 26.9124, "lon": 75.7873},
        {"city": "Lucknow", "state": "Uttar Pradesh", "country": "India", "country_code": "IN", "lat": 26.8467, "lon": 80.9462},
        {"city": "Kanpur", "state": "Uttar Pradesh", "country": "India", "country_code": "IN", "lat": 26.4499, "lon": 80.3319},
        {"city": "Nagpur", "state": "Maharashtra", "country": "India", "country_code": "IN", "lat": 21.1458, "lon": 79.0882},
        {"city": "Indore", "state": "Madhya Pradesh", "country": "India", "country_code": "IN", "lat": 22.7196, "lon": 75.8577},
        {"city": "Thane", "state": "Maharashtra", "country": "India", "country_code": "IN", "lat": 19.2183, "lon": 72.9781},
        {"city": "Bhopal", "state": "Madhya Pradesh", "country": "India", "country_code": "IN", "lat": 23.2599, "lon": 77.4126},
        {"city": "Visakhapatnam", "state": "Andhra Pradesh", "country": "India", "country_code": "IN", "lat": 17.6868, "lon": 83.2185},
        {"city": "Patna", "state": "Bihar", "country": "India", "country_code": "IN", "lat": 25.5941, "lon": 85.1376},
        {"city": "Vadodara", "state": "Gujarat", "country": "India", "country_code": "IN", "lat": 22.3072, "lon": 73.1812},
        {"city": "Ghaziabad", "state": "Uttar Pradesh", "country": "India", "country_code": "IN", "lat": 28.6692, "lon": 77.4538},
        {"city": "Ludhiana", "state": "Punjab", "country": "India", "country_code": "IN", "lat": 30.9010, "lon": 75.8573},
        {"city": "Agra", "state": "Uttar Pradesh", "country": "India", "country_code": "IN", "lat": 27.1767, "lon": 78.0081},
        {"city": "Nashik", "state": "Maharashtra", "country": "India", "country_code": "IN", "lat": 19.9975, "lon": 73.7898},
        {"city": "Faridabad", "state": "Haryana", "country": "India", "country_code": "IN", "lat": 28.4089, "lon": 77.3178},
        {"city": "Meerut", "state": "Uttar Pradesh", "country": "India", "country_code": "IN", "lat": 28.9845, "lon": 77.7064},
        {"city": "Rajkot", "state": "Gujarat", "country": "India", "country_code": "IN", "lat": 22.3039, "lon": 70.8022},
        {"city": "Varanasi", "state": "Uttar Pradesh", "country": "India", "country_code": "IN", "lat": 25.3176, "lon": 82.9739},
        {"city": "Srinagar", "state": "Jammu and Kashmir", "country": "India", "country_code": "IN", "lat": 34.0837, "lon": 74.7973},
        {"city": "Aurangabad", "state": "Maharashtra", "country": "India", "country_code": "IN", "lat": 19.8762, "lon": 75.3433},
        {"city": "Dhanbad", "state": "Jharkhand", "country": "India", "country_code": "IN", "lat": 23.7957, "lon": 86.4304},
        {"city": "Amritsar", "state": "Punjab", "country": "India", "country_code": "IN", "lat": 31.6340, "lon": 74.8723},
        {"city": "Navi Mumbai", "state": "Maharashtra", "country": "India", "country_code": "IN", "lat": 19.0330, "lon": 73.0297},
        {"city": "Allahabad", "state": "Uttar Pradesh", "country": "India", "country_code": "IN", "lat": 25.4358, "lon": 81.8463},
        {"city": "Ranchi", "state": "Jharkhand", "country": "India", "country_code": "IN", "lat": 23.3441, "lon": 85.3096},
        {"city": "Howrah", "state": "West Bengal", "country": "India", "country_code": "IN", "lat": 22.5958, "lon": 88.2636},
        {"city": "Coimbatore", "state": "Tamil Nadu", "country": "India", "country_code": "IN", "lat": 11.0168, "lon": 76.9558},
        {"city": "Jabalpur", "state": "Madhya Pradesh", "country": "India", "country_code": "IN", "lat": 23.1815, "lon": 79.9864},
        {"city": "Gwalior", "state": "Madhya Pradesh", "country": "India", "country_code": "IN", "lat": 26.2183, "lon": 78.1828},
        {"city": "Vijayawada", "state": "Andhra Pradesh", "country": "India", "country_code": "IN", "lat": 16.5062, "lon": 80.6480},
        {"city": "Jodhpur", "state": "Rajasthan", "country": "India", "country_code": "IN", "lat": 26.2389, "lon": 73.0243},
        {"city": "Madurai", "state": "Tamil Nadu", "country": "India", "country_code": "IN", "lat": 9.9252, "lon": 78.1198},
        {"city": "Raipur", "state": "Chhattisgarh", "country": "India", "country_code": "IN", "lat": 21.2514, "lon": 81.6296},
        {"city": "Kota", "state": "Rajasthan", "country": "India", "country_code": "IN", "lat": 25.2138, "lon": 75.8648},
        {"city": "Guwahati", "state": "Assam", "country": "India", "country_code": "IN", "lat": 26.1445, "lon": 91.7362},
        {"city": "Chandigarh", "state": "Chandigarh", "country": "India", "country_code": "IN", "lat": 30.7333, "lon": 76.7794},
        {"city": "Solapur", "state": "Maharashtra", "country": "India", "country_code": "IN", "lat": 17.6599, "lon": 75.9064},
        {"city": "Hubli", "state": "Karnataka", "country": "India", "country_code": "IN", "lat": 15.3647, "lon": 75.1240},
        {"city": "Mysore", "state": "Karnataka", "country": "India", "country_code": "IN", "lat": 12.2958, "lon": 76.6394},
        {"city": "Gurugram", "state": "Haryana", "country": "India", "country_code": "IN", "lat": 28.4595, "lon": 77.0266},
        {"city": "Noida", "state": "Uttar Pradesh", "country": "India", "country_code": "IN", "lat": 28.5355, "lon": 77.3910},
        {"city": "Aligarh", "state": "Uttar Pradesh", "country": "India", "country_code": "IN", "lat": 27.8974, "lon": 78.0880},
        {"city": "Jalandhar", "state": "Punjab", "country": "India", "country_code": "IN", "lat": 31.3260, "lon": 75.5762},
        {"city": "Bhubaneswar", "state": "Odisha", "country": "India", "country_code": "IN", "lat": 20.2961, "lon": 85.8245},
        {"city": "Salem", "state": "Tamil Nadu", "country": "India", "country_code": "IN", "lat": 11.6643, "lon": 78.1460},
        {"city": "Warangal", "state": "Telangana", "country": "India", "country_code": "IN", "lat": 17.9689, "lon": 79.5941},
        {"city": "Thiruvananthapuram", "state": "Kerala", "country": "India", "country_code": "IN", "lat": 8.5241, "lon": 76.9366},
        {"city": "Kochi", "state": "Kerala", "country": "India", "country_code": "IN", "lat": 9.9312, "lon": 76.2673},
        {"city": "Kozhikode", "state": "Kerala", "country": "India", "country_code": "IN", "lat": 11.2588, "lon": 75.7804},
        {"city": "Dehradun", "state": "Uttarakhand", "country": "India", "country_code": "IN", "lat": 30.3165, "lon": 78.0322},
        {"city": "Shimla", "state": "Himachal Pradesh", "country": "India", "country_code": "IN", "lat": 31.1048, "lon": 77.1734},
        {"city": "Haridwar", "state": "Uttarakhand", "country": "India", "country_code": "IN", "lat": 29.9457, "lon": 78.1642},
        {"city": "Jammu", "state": "Jammu and Kashmir", "country": "India", "country_code": "IN", "lat": 32.7266, "lon": 74.8570},
        {"city": "Panaji", "state": "Goa", "country": "India", "country_code": "IN", "lat": 15.4909, "lon": 73.8278},
        {"city": "Puducherry", "state": "Puducherry", "country": "India", "country_code": "IN", "lat": 11.9416, "lon": 79.8083},
        {"city": "Agartala", "state": "Tripura", "country": "India", "country_code": "IN", "lat": 23.8315, "lon": 91.2868},
        {"city": "Shillong", "state": "Meghalaya", "country": "India", "country_code": "IN", "lat": 25.5788, "lon": 91.8933},
        {"city": "Imphal", "state": "Manipur", "country": "India", "country_code": "IN", "lat": 24.8170, "lon": 93.9368},
        {"city": "Kohima", "state": "Nagaland", "country": "India", "country_code": "IN", "lat": 25.6751, "lon": 94.1086},
        {"city": "Aizawl", "state": "Mizoram", "country": "India", "country_code": "IN", "lat": 23.7271, "lon": 92.7176},
        {"city": "Itanagar", "state": "Arunachal Pradesh", "country": "India", "country_code": "IN", "lat": 27.0844, "lon": 93.6053},
        {"city": "Gangtok", "state": "Sikkim", "country": "India", "country_code": "IN", "lat": 27.3389, "lon": 88.6065},
        {"city": "Leh", "state": "Ladakh", "country": "India", "country_code": "IN", "lat": 34.1526, "lon": 77.5771},
        # Canada
        {"city": "Toronto", "state": "ON", "country": "Canada", "country_code": "CA", "lat": 43.6532, "lon": -79.3832},
        {"city": "Vancouver", "state": "BC", "country": "Canada", "country_code": "CA", "lat": 49.2827, "lon": -123.1207},
        {"city": "Montreal", "state": "QC", "country": "Canada", "country_code": "CA", "lat": 45.5017, "lon": -73.5673},
        {"city": "Calgary", "state": "AB", "country": "Canada", "country_code": "CA", "lat": 51.0447, "lon": -114.0719},
        {"city": "Ottawa", "state": "ON", "country": "Canada", "country_code": "CA", "lat": 45.4215, "lon": -75.6972},
        {"city": "Edmonton", "state": "AB", "country": "Canada", "country_code": "CA", "lat": 53.5461, "lon": -113.4938},
        # Europe / UK
        {"city": "London", "state": "England", "country": "United Kingdom", "country_code": "GB", "lat": 51.5074, "lon": -0.1278},
        {"city": "Manchester", "state": "England", "country": "United Kingdom", "country_code": "GB", "lat": 53.4808, "lon": -2.2426},
        {"city": "Birmingham", "state": "England", "country": "United Kingdom", "country_code": "GB", "lat": 52.4862, "lon": -1.8904},
        {"city": "Frankfurt", "state": "Hessen", "country": "Germany", "country_code": "DE", "lat": 50.1109, "lon": 8.6821},
        {"city": "Berlin", "state": "Berlin", "country": "Germany", "country_code": "DE", "lat": 52.5200, "lon": 13.4050},
        {"city": "Munich", "state": "Bavaria", "country": "Germany", "country_code": "DE", "lat": 48.1351, "lon": 11.5820},
        {"city": "Paris", "state": "Île-de-France", "country": "France", "country_code": "FR", "lat": 48.8566, "lon": 2.3522},
        {"city": "Amsterdam", "state": "North Holland", "country": "Netherlands", "country_code": "NL", "lat": 52.3676, "lon": 4.9041},
        # Australia
        {"city": "Sydney", "state": "NSW", "country": "Australia", "country_code": "AU", "lat": -33.8688, "lon": 151.2093},
        {"city": "Melbourne", "state": "VIC", "country": "Australia", "country_code": "AU", "lat": -37.8136, "lon": 144.9631},
        {"city": "Brisbane", "state": "QLD", "country": "Australia", "country_code": "AU", "lat": -27.4698, "lon": 153.0251},
        {"city": "Perth", "state": "WA", "country": "Australia", "country_code": "AU", "lat": -31.9505, "lon": 115.8605},
        # Latin America
        {"city": "Mexico City", "state": "CDMX", "country": "Mexico", "country_code": "MX", "lat": 19.4326, "lon": -99.1332},
        {"city": "São Paulo", "state": "SP", "country": "Brazil", "country_code": "BR", "lat": -23.5505, "lon": -46.6333},
        {"city": "Buenos Aires", "state": "CABA", "country": "Argentina", "country_code": "AR", "lat": -34.6037, "lon": -58.3816},
    ]

    def get(self, request, *args, **kwargs):
        q = request.query_params.get("q", "").strip().lower()
        country_filter = request.query_params.get("country", "ALL").strip().upper()
        if not q or len(q) < 1:
            return Response([])

        clean_q = "".join(ch for ch in q if ch.isalnum() or ch in " ,-")
        tokens = [t.strip() for t in clean_q.split(",") if t.strip()]
        city_token = tokens[0] if tokens else clean_q
        state_token = tokens[1].upper() if len(tokens) > 1 else ""

        results = []
        seen = set()

        # 1. Search Global Hubs if not strictly US
        if country_filter != "US":
            for hub in self.GLOBAL_HUBS:
                if country_filter not in ("ALL", "") and hub["country_code"] != country_filter:
                    continue
                hay = f"{hub['city']} {hub['state']} {hub['country']}".lower()
                if all(tok.lower() in hay for tok in tokens):
                    key = f"{hub['city']}_{hub['country_code']}"
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "city": hub["city"],
                            "state": hub["state"],
                            "country": hub["country"],
                            "country_code": hub["country_code"],
                            "label": f"{hub['city']}, {hub['state']}, {hub['country_code']}",
                            "display_name": f"{hub['city']}, {hub['state']}, {hub['country']}",
                            "lat": hub["lat"],
                            "lon": hub["lon"],
                        })

            # Also query Open-Meteo for worldwide coverage if results < 5
            if len(results) < 5 and len(city_token) >= 2:
                try:
                    om_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city_token)}&count=10&format=json"
                    om_req = urllib.request.Request(om_url, headers={"User-Agent": "SpotterFuelRouting/1.0"})
                    with urllib.request.urlopen(om_req, timeout=3.0) as om_resp:
                        om_data = json.load(om_resp)
                        for item in om_data.get("results", []):
                            cc = (item.get("country_code") or "").upper()
                            if country_filter not in ("ALL", "") and cc != country_filter:
                                continue
                            c_name = item.get("name", "")
                            st_name = item.get("admin1", "")
                            country_name = item.get("country", "")
                            key = f"{c_name}_{cc}"
                            if key not in seen:
                                seen.add(key)
                                results.append({
                                    "city": c_name,
                                    "state": st_name,
                                    "country": country_name,
                                    "country_code": cc,
                                    "label": f"{c_name}, {st_name}, {cc}" if st_name else f"{c_name}, {country_name}",
                                    "display_name": f"{c_name}, {st_name}, {country_name}",
                                    "lat": item.get("latitude"),
                                    "lon": item.get("longitude"),
                                })
                except Exception as e:
                    logger.debug("Open-Meteo geocoding fallback error: %s", e)

        # 2. Search local 40,000+ US cities database
        if country_filter in ("ALL", "US", ""):
            from fuel_routing.services.geocoding import _get_local_cities
            cities_db = _get_local_cities()
            norm_target = "".join(ch for ch in city_token.lower() if ch.isalnum())

            prefix_matches = []
            contains_matches = []

            for k, (lat, lon) in cities_db.items():
                parts = k.split("_")
                if len(parts) >= 2:
                    city_key, st = parts[0], parts[1]
                    if state_token and st != state_token:
                        continue
                    item = {
                        "city": city_key.title(),
                        "state": st,
                        "country": "United States",
                        "country_code": "US",
                        "label": f"{city_key.title()}, {st}, USA",
                        "display_name": f"{city_key.title()}, {st}, United States",
                        "lat": lat,
                        "lon": lon,
                    }
                    if city_key.startswith(norm_target):
                        prefix_matches.append(item)
                    elif norm_target in city_key:
                        contains_matches.append(item)

            for item in sorted(prefix_matches, key=lambda x: (len(x["city"]), x["city"])) + contains_matches:
                key = f"{item['city']}_{item['state']}"
                if key not in seen:
                    seen.add(key)
                    results.append(item)
                    if len(results) >= 25:
                        break

        return Response(results[:25])


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
