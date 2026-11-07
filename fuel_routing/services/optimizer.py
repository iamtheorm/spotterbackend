"""
Fuel stop optimization service.
Computes globally optimal refueling stops along a highway route to minimize total fuel cost
under vehicle range constraints (500 miles max range, 10 MPG = 50 gal tank capacity).

Uses linear programming (scipy.optimize.linprog with HiGHS simplex/interior-point)
for exact mathematical optimality and millisecond performance.
"""
import logging
from typing import Any, Dict, List, Optional
import numpy as np
from scipy.optimize import linprog

logger = logging.getLogger(__name__)

def optimize_fuel_stops(
    total_distance_miles: float,
    candidate_stations: List[Dict[str, Any]],
    max_range_miles: float = 500.0,
    mpg: float = 10.0,
    initial_fuel_miles: float = 500.0,
    ensure_end_full: bool = False
) -> Dict[str, Any]:
    """
    Optimizes fuel stops along the route.

    Parameters:
    - total_distance_miles: Total driving distance of the route.
    - candidate_stations: List of candidate fuel stations along the route,
      each with 'mile' (distance from start) and 'price' ($/gallon).
    - max_range_miles: Maximum range of vehicle on a full tank (default: 500 miles).
    - mpg: Fuel efficiency in miles per gallon (default: 10 MPG).
    - initial_fuel_miles: Range available in the tank at start (default: 500 miles).
    - ensure_end_full: If True, vehicle purchases enough fuel to also end with a full tank.
      If False (default), vehicle purchases only the fuel needed to reach destination safely.

    Returns a dict with:
    - success: bool
    - fuel_stops: List of selected stops with gallons, cost, and tank levels.
    - total_fuel_consumed_gallons: Total fuel used for the trip distance.
    - total_fuel_purchased_gallons: Total fuel bought at the optimal stops.
    - total_fuel_cost_dollars: Total money spent on fuel.
    - initial_fuel_gallons: Fuel in tank at departure.
    - ending_fuel_gallons: Fuel remaining in tank at destination.
    """
    tank_capacity_gallons = max_range_miles / mpg  # 50.0 gal
    initial_fuel_gallons = min(initial_fuel_miles / mpg, tank_capacity_gallons)
    total_consumed_gallons = round(total_distance_miles / mpg, 2)

    # If route is shorter than initial fuel range and we don't need to end full
    if total_distance_miles <= initial_fuel_miles and not ensure_end_full:
        remaining_gallons = round(initial_fuel_gallons - total_consumed_gallons, 2)
        return {
            "success": True,
            "fuel_stops": [],
            "total_stops": 0,
            "total_fuel_consumed_gallons": total_consumed_gallons,
            "total_fuel_purchased_gallons": 0.0,
            "total_fuel_cost_dollars": 0.0,
            "initial_fuel_gallons": initial_fuel_gallons,
            "ending_fuel_gallons": remaining_gallons,
            "message": f"Route ({total_distance_miles:.1f} mi) is within initial vehicle range ({initial_fuel_miles:.1f} mi). No refueling stops required."
        }

    # Filter and deduplicate candidate stations along the route (0 < mile < total_distance_miles)
    # If multiple stations are within 1 mile of each other, retain the cheapest one
    valid_cands = [s for s in candidate_stations if 0.0 < s["mile"] < total_distance_miles]

    if not valid_cands:
        # Check if direct distance exceeds initial fuel
        if total_distance_miles > initial_fuel_miles:
            return {
                "success": False,
                "error": f"Route distance ({total_distance_miles:.1f} mi) exceeds range ({initial_fuel_miles:.1f} mi), but no fuel stations were found along the corridor."
            }

    # Cluster stations that are extremely close (< 1 mile apart) and keep the lowest price
    clustered: List[Dict[str, Any]] = []
    for s in valid_cands:
        if clustered and abs(s["mile"] - clustered[-1]["mile"]) < 1.0:
            if s["price"] < clustered[-1]["price"]:
                clustered[-1] = s
        else:
            clustered.append(s)

    N = len(clustered)
    if N == 0:
        return {
            "success": False,
            "error": "No reachable fuel stations available along the route."
        }

    miles = np.array([s["mile"] for s in clustered], dtype=np.float64)
    prices = np.array([s["price"] for s in clustered], dtype=np.float64)

    # Check maximum gap between consecutive points to ensure feasibility
    checkpoints = [0.0] + list(miles) + [total_distance_miles]
    max_gap = max(checkpoints[i] - checkpoints[i-1] for i in range(1, len(checkpoints)))
    if max_gap > max_range_miles:
        logger.warning("Gap between stations (%.1f mi) exceeds max range (%.1f mi)", max_gap, max_range_miles)
        # Note: LP will report infeasible if unreachable

    # Formulate Linear Program:
    # Variables x_0 ... x_{N-1}: gallons of fuel purchased at station i
    # Objective: Minimize sum_{i=0}^{N-1} prices[i] * x[i]
    #
    # Constraints:
    # Fuel level in tank right before station j (for j = 0..N-1):
    #   fuel_before(j) = initial_fuel_gallons + sum_{i < j} x[i] - miles[j] / mpg >= 0
    #   => - sum_{i < j} x[i] <= initial_fuel_gallons - miles[j] / mpg
    #
    # Fuel level in tank right after station j:
    #   fuel_after(j) = initial_fuel_gallons + sum_{i <= j} x[i] - miles[j] / mpg <= tank_capacity_gallons
    #   => sum_{i <= j} x[i] <= tank_capacity_gallons - initial_fuel_gallons + miles[j] / mpg
    #
    # Fuel level at Destination:
    #   fuel_dest = initial_fuel_gallons + sum_{i < N} x[i] - total_distance_miles / mpg >= 0
    #   => - sum_{i < N} x[i] <= initial_fuel_gallons - total_distance_miles / mpg
    #
    # If ensure_end_full is True:
    #   fuel_dest == tank_capacity_gallons

    A_ub = []
    b_ub = []

    # 1. Non-negative fuel before reaching station j
    for j in range(N):
        row = [-1.0 if i < j else 0.0 for i in range(N)]
        A_ub.append(row)
        b_ub.append(initial_fuel_gallons - miles[j] / mpg)

    # 2. Tank capacity upper bound right after fueling at station j
    for j in range(N):
        row = [1.0 if i <= j else 0.0 for i in range(N)]
        A_ub.append(row)
        b_ub.append(tank_capacity_gallons - initial_fuel_gallons + miles[j] / mpg)

    # 3. Non-negative fuel arriving at destination
    dest_req = initial_fuel_gallons - total_distance_miles / mpg
    if ensure_end_full:
        # Require ending with full tank
        A_ub.append([-1.0 for _ in range(N)])
        b_ub.append(dest_req - tank_capacity_gallons)
    else:
        A_ub.append([-1.0 for _ in range(N)])
        b_ub.append(dest_req)

    # Variable bounds: 0 <= x[i] <= tank_capacity_gallons
    bounds = [(0.0, tank_capacity_gallons) for _ in range(N)]

    res = linprog(c=prices, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")

    if not res.success:
        return {
            "success": False,
            "error": f"Refueling optimization could not find a feasible path. Gaps between available stations may exceed the {max_range_miles} mile vehicle range."
        }

    bought_gallons = res.x
    selected_stops = []
    running_fuel = initial_fuel_gallons

    for i in range(N):
        dist_from_prev = miles[i] - (miles[i-1] if i > 0 else 0.0)
        fuel_before = running_fuel - (dist_from_prev / mpg)
        gallons = bought_gallons[i]

        if gallons > 0.05:  # Significant fuel stop (> 0.05 gal)
            st = clustered[i]
            cost = round(float(gallons * st["price"]), 2)
            fuel_after = min(fuel_before + gallons, tank_capacity_gallons)

            selected_stops.append({
                "stop_index": len(selected_stops) + 1,
                "opis_id": st["opis_id"],
                "truckstop_name": st["name"],
                "address": st["address"],
                "city": st["city"],
                "state": st["state"],
                "retail_price": round(float(st["price"]), 3),
                "distance_along_route_miles": round(float(st["mile"]), 2),
                "detour_miles": round(float(st.get("detour_miles", 0.0)), 2),
                "coordinates": {
                    "latitude": round(float(st["lat"]), 6),
                    "longitude": round(float(st["lon"]), 6)
                },
                "fuel_before_arrival_gallons": round(float(max(0.0, fuel_before)), 2),
                "fuel_pumped_gallons": round(float(gallons), 2),
                "fuel_after_refueling_gallons": round(float(fuel_after), 2),
                "cost_dollars": cost
            })
            running_fuel = fuel_after
        else:
            running_fuel = fuel_before

    # Calculate final fuel at destination
    last_stop_mile = selected_stops[-1]["distance_along_route_miles"] if selected_stops else 0.0
    dist_to_dest = total_distance_miles - last_stop_mile
    final_fuel_gallons = round(max(0.0, running_fuel - dist_to_dest / mpg), 2)

    total_cost = round(sum(s["cost_dollars"] for s in selected_stops), 2)
    total_purchased = round(sum(s["fuel_pumped_gallons"] for s in selected_stops), 2)

    return {
        "success": True,
        "fuel_stops": selected_stops,
        "total_stops": len(selected_stops),
        "total_fuel_consumed_gallons": total_consumed_gallons,
        "total_fuel_purchased_gallons": total_purchased,
        "total_fuel_cost_dollars": total_cost,
        "initial_fuel_gallons": initial_fuel_gallons,
        "ending_fuel_gallons": final_fuel_gallons,
        "vehicle_parameters": {
            "max_range_miles": max_range_miles,
            "fuel_efficiency_mpg": mpg,
            "tank_capacity_gallons": tank_capacity_gallons
        }
    }

