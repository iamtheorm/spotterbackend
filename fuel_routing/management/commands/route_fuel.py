"""
Django management command to calculate optimal fuel stops from the CLI.
Example:
    python manage.py route_fuel --start "New York, NY" --finish "Atlanta, GA"
"""
from django.core.management.base import BaseCommand
from fuel_routing.services.geocoding import geocode_location
from fuel_routing.services.osrm_client import get_driving_route
from fuel_routing.services.spatial_index import find_candidate_stations_along_route
from fuel_routing.services.optimizer import optimize_fuel_stops


class Command(BaseCommand):
    help = "Compute optimal highway driving route and refueling stops between two US locations."

    def add_arguments(self, parser):
        parser.add_argument("--start", type=str, required=True, help="Origin location (e.g. 'New York, NY')")
        parser.add_argument("--finish", type=str, required=True, help="Destination location (e.g. 'Los Angeles, CA')")
        parser.add_argument("--mpg", type=float, default=10.0, help="Fuel efficiency in MPG (default: 10)")
        parser.add_argument("--range", type=float, default=500.0, help="Max vehicle range in miles (default: 500)")
        parser.add_argument("--buffer", type=float, default=10.0, help="Station search corridor in miles (default: 10)")

    def handle(self, *args, **options):
        start_q = options["start"]
        finish_q = options["finish"]
        mpg = options["mpg"]
        max_range = options["range"]
        buffer_miles = options["buffer"]

        self.stdout.write(self.style.MIGRATE_HEADING(f"\nCalculating Optimal Route: {start_q} -> {finish_q}\n"))

        # 1. Geocode
        try:
            s_lat, s_lon, s_label = geocode_location(start_q)
            f_lat, f_lon, f_label = geocode_location(finish_q)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Geocoding error: {e}"))
            return

        self.stdout.write(f"  • Start : {s_label} ({s_lat:.4f}, {s_lon:.4f})")
        self.stdout.write(f"  • Finish: {f_label} ({f_lat:.4f}, {f_lon:.4f})")

        # 2. Driving Route via OSRM
        try:
            route = get_driving_route(s_lat, s_lon, f_lat, f_lon)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Routing error: {e}"))
            return

        dist = route["distance_miles"]
        dur = route["duration_hours"]
        h = int(dur)
        m = int(round((dur - h) * 60))
        self.stdout.write(f"  • Route : {dist:.1f} miles (~{h}h {m}m)")

        # 3. Spatial Search
        cands = find_candidate_stations_along_route(
            route_coords=route["coordinates"],
            total_distance_miles=dist,
            buffer_miles=buffer_miles
        )
        self.stdout.write(f"  • Corridor: {len(cands)} stations found within {buffer_miles} miles of route")

        # 4. Optimization
        opt = optimize_fuel_stops(
            total_distance_miles=dist,
            candidate_stations=cands,
            max_range_miles=max_range,
            mpg=mpg,
            initial_fuel_miles=max_range
        )

        if not opt["success"]:
            self.stderr.write(self.style.ERROR(f"Optimization failed: {opt.get('error')}"))
            return

        # Results Summary
        self.stdout.write(self.style.SUCCESS(f"\n======================================================="))
        self.stdout.write(self.style.SUCCESS(f"  OPTIMAL FUELING SCHEDULE"))
        self.stdout.write(self.style.SUCCESS(f"======================================================="))
        self.stdout.write(f"  • Total Fuel Consumed : {opt['total_fuel_consumed_gallons']:.2f} gallons")
        self.stdout.write(f"  • Total Fuel Purchased: {opt['total_fuel_purchased_gallons']:.2f} gallons")
        self.stdout.write(self.style.WARNING(f"  • Total Fuel Cost     : ${opt['total_fuel_cost_dollars']:.2f}"))
        self.stdout.write(f"  • Refueling Stops     : {opt['total_stops']} stops\n")

        stops = opt.get("fuel_stops", [])
        if not stops:
            self.stdout.write(self.style.NOTICE("  No refueling stops required. Trip is within initial tank range.\n"))
            return

        fmt = "{:<5} {:<32} {:<18} {:<10} {:<10} {:<10}"
        self.stdout.write(self.style.MIGRATE_LABEL(fmt.format("Stop", "Truckstop Name", "Location", "Price", "Pumped", "Cost")))
        self.stdout.write("-" * 90)

        for s in stops:
            loc = f"{s['city']}, {s['state']}"
            price_str = f"${s['retail_price']:.3f}/g"
            pumped_str = f"{s['fuel_pumped_gallons']:.1f} gal"
            cost_str = f"${s['cost_dollars']:.2f}"
            name = s['truckstop_name'][:30]
            self.stdout.write(fmt.format(f"#{s['stop_index']}", name, loc, price_str, pumped_str, cost_str))

        self.stdout.write("-" * 90 + "\n")

