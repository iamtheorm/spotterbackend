"""
Unit and Integration Tests for Spotter Fuel Routing System.
"""
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from rest_framework import status

from fuel_routing.services.geocoding import geocode_location, parse_coordinates
from fuel_routing.services.spatial_index import get_spatial_index, find_candidate_stations_along_route
from fuel_routing.services.optimizer import optimize_fuel_stops
from fuel_routing.services.osrm_client import get_driving_route


class GeocodingTests(TestCase):
    def test_local_cities_lookup(self):
        lat, lon, label = geocode_location("Chicago, IL")
        self.assertAlmostEqual(lat, 41.8781, delta=0.5)
        self.assertAlmostEqual(lon, -87.6298, delta=0.5)
        self.assertIn("Chicago", label)

    def test_coordinate_parsing(self):
        coord = parse_coordinates("40.7128, -74.0060")
        self.assertIsNotNone(coord)
        lat, lon, _ = coord
        self.assertAlmostEqual(lat, 40.7128, delta=0.001)
        self.assertAlmostEqual(lon, -74.0060, delta=0.001)

    def test_invalid_query_raises_error(self):
        with self.assertRaises(ValueError):
            geocode_location("")


class SpatialIndexTests(TestCase):
    def test_index_loaded(self):
        stations, tree = get_spatial_index()
        self.assertGreater(len(stations), 5000)
        self.assertIsNotNone(tree)

    def test_candidate_search_along_mock_route(self):
        # Route from NYC area to Philadelphia area (~100 miles)
        mock_coords = [
            [40.7128, -74.0060],
            [40.4000, -74.3000],
            [40.1000, -74.7000],
            [39.9526, -75.1652]
        ]
        cands = find_candidate_stations_along_route(
            route_coords=mock_coords,
            total_distance_miles=95.0,
            buffer_miles=15.0
        )
        self.assertIsInstance(cands, list)
        self.assertGreater(len(cands), 0)
        # Verify candidates have required keys
        for c in cands[:5]:
            self.assertIn("opis_id", c)
            self.assertIn("price", c)
            self.assertIn("mile", c)
            self.assertIn("detour_miles", c)


class FuelOptimizerTests(TestCase):
    def test_short_route_no_stops_needed(self):
        # 300 miles is within 500 miles max range
        res = optimize_fuel_stops(
            total_distance_miles=300.0,
            candidate_stations=[{"mile": 100.0, "price": 3.0, "name": "Stop 1", "opis_id": 1, "city": "A", "state": "OH", "lat": 40.0, "lon": -80.0}],
            max_range_miles=500.0,
            mpg=10.0,
            initial_fuel_miles=500.0
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["total_stops"], 0)
        self.assertEqual(res["total_fuel_cost_dollars"], 0.0)
        self.assertEqual(res["total_fuel_consumed_gallons"], 30.0)

    def test_medium_route_requires_refueling(self):
        # 800 miles route with candidate stations at mile 250 and 450
        candidates = [
            {"opis_id": 1, "name": "Cheap Stop", "address": "1 Main", "city": "CityA", "state": "VA", "price": 2.80, "mile": 250.0, "lat": 38.0, "lon": -78.0},
            {"opis_id": 2, "name": "Expensive Stop", "address": "2 Main", "city": "CityB", "state": "NC", "price": 3.80, "mile": 400.0, "lat": 36.0, "lon": -79.0},
        ]
        res = optimize_fuel_stops(
            total_distance_miles=800.0,
            candidate_stations=candidates,
            max_range_miles=500.0,
            mpg=10.0,
            initial_fuel_miles=500.0
        )
        self.assertTrue(res["success"])
        self.assertGreaterEqual(res["total_stops"], 1)
        self.assertEqual(res["total_fuel_consumed_gallons"], 80.0)
        self.assertGreater(res["total_fuel_purchased_gallons"], 0.0)
        self.assertGreater(res["total_fuel_cost_dollars"], 0.0)


class APITests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_health_check(self):
        resp = self.client.get(reverse("api-health"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertGreater(data["indexed_stations"], 5000)

    def test_location_search(self):
        resp = self.client.get(reverse("api-location-search"), {"q": "Dallas", "country": "US"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.json()
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["city"], "Dallas")
        self.assertEqual(results[0]["state"], "TX")

    def test_missing_params(self):
        resp = self.client.get(reverse("api-route"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("fuel_routing.views.find_candidate_stations_along_route")
    @patch("fuel_routing.views.get_driving_route")
    def test_api_route_mocked(self, mock_osrm, mock_cands):
        # Mock OSRM driving route
        mock_osrm.return_value = {
            "distance_miles": 850.0,
            "duration_seconds": 50000.0,
            "duration_hours": 13.89,
            "coordinates": [
                [40.7128, -74.0060],
                [38.0000, -78.0000],
                [35.0000, -82.0000],
                [33.7490, -84.3880]
            ],
            "geojson_geometry": {
                "type": "LineString",
                "coordinates": [[-74.0060, 40.7128], [-84.3880, 33.7490]]
            },
            "bounds": [33.7490, -84.3880, 40.7128, -74.0060]
        }
        mock_cands.return_value = [
            {"opis_id": 101, "name": "Stop A", "address": "1 Hwy", "city": "CityA", "state": "VA", "price": 2.89, "mile": 250.0, "detour_miles": 1.2, "lat": 38.0, "lon": -78.0},
            {"opis_id": 102, "name": "Stop B", "address": "2 Hwy", "city": "CityB", "state": "NC", "price": 2.79, "mile": 500.0, "detour_miles": 2.0, "lat": 35.5, "lon": -80.5},
            {"opis_id": 103, "name": "Stop C", "address": "3 Hwy", "city": "CityC", "state": "SC", "price": 2.99, "mile": 700.0, "detour_miles": 0.5, "lat": 34.0, "lon": -82.0}
        ]

        resp = self.client.get(reverse("api-route"), {
            "start": "New York, NY",
            "finish": "Atlanta, GA"
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("route", data)
        self.assertIn("fuel_summary", data)
        self.assertIn("fuel_stops", data)
        self.assertIn("geojson", data)
        self.assertIn("execution_metrics", data)

        fuel = data["fuel_summary"]
        self.assertEqual(fuel["vehicle_mpg"], 10.0)
        self.assertEqual(fuel["max_range_miles"], 500.0)
        self.assertEqual(fuel["total_fuel_consumed_gallons"], 85.0)

    def test_map_view_renders(self):
        resp = self.client.get(reverse("map-view"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertContains(resp, "Spotter Fuel Router")
        self.assertContains(resp, "leaflet")
