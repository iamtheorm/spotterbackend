"""
Integration test for geocoding -> OSRM routing -> spatial search -> LP fuel optimization.
"""
import time
from fuel_routing.services.geocoding import geocode_location
from fuel_routing.services.osrm_client import get_driving_route
from fuel_routing.services.spatial_index import find_candidate_stations_along_route
from fuel_routing.services.optimizer import optimize_fuel_stops

def test_route(start_query, finish_query):
    print(f"\n==========================================")
    print(f"Testing Route: {start_query} -> {finish_query}")
    print(f"==========================================")
    t0 = time.time()

    # 1. Geocode
    s_lat, s_lon, s_label = geocode_location(start_query)
    f_lat, f_lon, f_label = geocode_location(finish_query)
    t_geo = time.time()
    print(f"Geocoded in {(t_geo - t0)*1000:.1f}ms:")
    print(f"  Start: {s_label} ({s_lat:.4f}, {s_lon:.4f})")
    print(f"  Finish: {f_label} ({f_lat:.4f}, {f_lon:.4f})")

    # 2. OSRM Route
    route_data = get_driving_route(s_lat, s_lon, f_lat, f_lon)
    t_route = time.time()
    dist = route_data["distance_miles"]
    dur = route_data["duration_hours"]
    pts = len(route_data["coordinates"])
    print(f"OSRM Route in {(t_route - t_geo)*1000:.1f}ms:")
    print(f"  Distance: {dist} miles, Duration: {dur} hrs, Polyline vertices: {pts}")

    # 3. Spatial search
    cands = find_candidate_stations_along_route(
        route_coords=route_data["coordinates"],
        total_distance_miles=dist,
        buffer_miles=10.0
    )
    t_spatial = time.time()
    print(f"Spatial corridor search in {(t_spatial - t_route)*1000:.1f}ms:")
    print(f"  Candidate stations within 10 mi: {len(cands)}")

    # 4. Optimization
    opt = optimize_fuel_stops(
        total_distance_miles=dist,
        candidate_stations=cands,
        max_range_miles=500.0,
        mpg=10.0,
        initial_fuel_miles=500.0
    )
    t_opt = time.time()
    print(f"Optimization in {(t_opt - t_spatial)*1000:.1f}ms:")
    print(f"  Total time: {(t_opt - t0)*1000:.1f}ms")
    print(f"  Success: {opt['success']}")
    print(f"  Stops recommended: {opt.get('total_stops', 0)}")
    print(f"  Total Fuel Consumed: {opt.get('total_fuel_consumed_gallons', 0)} gal")
    print(f"  Total Fuel Purchased: {opt.get('total_fuel_purchased_gallons', 0)} gal")
    print(f"  Total Fuel Cost: ${opt.get('total_fuel_cost_dollars', 0):.2f}")

    for s in opt.get("fuel_stops", []):
        print(f"    Stop #{s['stop_index']}: {s['truckstop_name']} ({s['city']}, {s['state']})")
        print(f"      Mile {s['distance_along_route_miles']} | Price: ${s['retail_price']}/gal | Pumped: {s['fuel_pumped_gallons']} gal | Cost: ${s['cost_dollars']}")

if __name__ == "__main__":
    # Test 1: Short route (< 500 miles)
    test_route("New York, NY", "Washington, DC")
    # Test 2: Medium route (> 500 miles)
    test_route("New York, NY", "Atlanta, GA")
    # Test 3: Cross country route (> 2500 miles)
    test_route("New York, NY", "Los Angeles, CA")

