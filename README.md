# Spotter Fuel Routing API ⛽🗺️

A high-performance Django REST API and interactive web application that plans driving routes between any two locations in the USA, optimizes refueling stops based on real-time retail fuel prices, and computes the total fuel expense under vehicle range and efficiency constraints.

---

## 🚀 Key Highlights & Requirements Fulfillment

- **Framework**: Built with **latest stable Django** (Django 5/6) and Django REST Framework.
- **Constraints**:
  - Vehicle maximum range: **500 miles**.
  - Vehicle fuel efficiency: **10 miles per gallon (MPG)**.
- **Minimal External API Calls**:
  - **Exactly 1 external API call** per request for highway routing using the free [Project OSRM](http://project-osrm.org/) driving API.
  - **0 external API calls for fuel stops**: All 6,600+ US truckstops from the provided OPIS dataset are pre-geocoded and indexed in memory.
  - **Instant local geocoding**: Over 40,000+ US cities and places are indexed locally for zero-latency lookups, with graceful fallback to Photon/Nominatim only when necessary.
- **Blazing Fast Performance**:
  - Local geocoding: `< 5 ms`
  - Spatial corridor search across 35,000+ route vertices: `< 35 ms`
  - Mathematical optimization: `< 15 ms`
  - Total compute time: `< 55 ms` (overall response time ~1.5s dominated by the single OSRM network roundtrip).
- **Global Optimality**:
  - Uses Linear Programming (`scipy.optimize.linprog` with the **HiGHS** dual-simplex solver) to guarantee the globally minimum fuel cost while respecting vehicle capacity constraints ($0 \le \text{fuel} \le 50\text{ gallons}$) at all points along the route.
- **Interactive Visual Map**:
  - A modern Leaflet-based interactive web interface at `/` and `/map/` rendering the driving polyline, start/finish flags, and numbered fuel stop markers with pricing and gallon breakdown popups.

---

## 🛠️ Architecture & Optimization Pipeline

```
[User Input: Start & Finish]
            │
            ▼
┌───────────────────────────────┐
│ 1. US City / Coordinate Resolver│ ◄── 40,000+ US Places Local Index (< 5ms)
└───────────┬───────────────────┘
            │ (lat, lon) coordinates
            ▼
┌───────────────────────────────┐
│ 2. OSRM Free Routing Service  │ ◄── Single API call for full geometry & distance
└───────────┬───────────────────┘
            │ High-resolution polyline
            ▼
┌───────────────────────────────┐
│ 3. 3D Spherical cKDTree Index │ ◄── Corridor search within 10 miles of highway (< 35ms)
└───────────┬───────────────────┘
            │ Candidate stations sorted by route mile marker
            ▼
┌───────────────────────────────┐
│ 4. HiGHS Linear Program Solver │ ◄── Globally optimal refueling stops & gallons (< 15ms)
└───────────┬───────────────────┘
            │
            ▼
[JSON API Response & Interactive Leaflet Map]
```

### Mathematical LP Formulation

For a route of distance $D$ with candidate stations at mile markers $d_1 < d_2 < \dots < d_N$:
$$\min \sum_{i=1}^N \text{price}_i \times x_i$$
Subject to:
1. **Never run empty**:
   $$\text{fuel}_0 + \sum_{i < j} x_i \ge \frac{d_j}{\text{mpg}} \quad \forall j \in \{1, \dots, N, \text{Finish}\}$$
2. **Never exceed tank capacity** (50 gallons):
   $$\text{fuel}_0 + \sum_{i \le j} x_i - \frac{d_j}{\text{mpg}} \le 50 \quad \forall j \in \{1, \dots, N\}$$
3. **Non-negativity & capacity bounds**:
   $$0 \le x_i \le 50 \quad \forall i$$

---

## 📦 Installation & Setup

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.12)
- Virtual environment tool (`venv`)

### 2. Clone and Setup Environment

```bash
# Clone the repository
git clone <repo-url>
cd spotterbackend

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Run Database Migrations

```bash
python manage.py migrate
```

*(Note: The preprocessed station databases `data/stations.json` and `data/cities_lookup.json` are already included in the repository. If you ever want to rebuild them from the raw CSV, simply run `python scripts/build_station_db.py`).*

### 4. Run Development Server

```bash
python manage.py runserver 0.0.0.0:8000
```

Access the interactive map in your browser at:
**[http://localhost:8000/](http://localhost:8000/)** or **[http://localhost:8000/map/](http://localhost:8000/map/)**

---

## 📡 API Reference

### 1. Calculate Route and Optimal Fuel Stops

**Endpoint**: `GET /api/route/` or `POST /api/route/`

#### Query Parameters / Request Body

| Parameter | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `start` | `string` | **Yes** | - | Origin location (e.g. `"New York, NY"` or `"40.7128,-74.0060"`) |
| `finish` | `string` | **Yes** | - | Destination location (e.g. `"Los Angeles, CA"` or `"34.0522,-118.2437"`) |
| `mpg` | `float` | No | `10.0` | Vehicle fuel efficiency in miles per gallon |
| `max_range` | `float` | No | `500.0` | Maximum range of vehicle in miles on a full tank |
| `buffer_miles`| `float` | No | `10.0` | Search corridor radius in miles off the highway |
| `initial_fuel_miles` | `float` | No | `500.0` | Initial fuel in tank at start (miles of range) |
| `fuel_policy` | `string` | No | `"standard"` | `"standard"` (buys fuel needed to reach destination) or `"replace_consumed"` (ends with full tank) |

#### Example Request

```bash
curl -X GET "http://localhost:8000/api/route/?start=New+York%2C+NY&finish=Atlanta%2C+GA"
```

#### Example Response (Truncated for readability)

```json
{
  "status": "success",
  "route": {
    "start": {
      "query": "New York, NY",
      "label": "New York, NY",
      "latitude": 40.662712,
      "longitude": -73.938677
    },
    "finish": {
      "query": "Atlanta, GA",
      "label": "Atlanta, GA",
      "latitude": 33.748995,
      "longitude": -84.387982
    },
    "distance_miles": 865.06,
    "duration_hours": 17.06,
    "duration_formatted": "17 hr 4 min",
    "bounds": [33.749, -84.388, 40.7128, -73.9387]
  },
  "fuel_summary": {
    "vehicle_mpg": 10.0,
    "max_range_miles": 500.0,
    "tank_capacity_gallons": 50.0,
    "initial_fuel_gallons": 50.0,
    "ending_fuel_gallons": 0.0,
    "total_fuel_consumed_gallons": 86.51,
    "total_fuel_purchased_gallons": 36.51,
    "total_money_spent_dollars": 104.51,
    "average_fuel_cost_per_gallon": 2.863,
    "total_refueling_stops": 2
  },
  "fuel_stops": [
    {
      "stop_index": 1,
      "opis_id": 1421,
      "truckstop_name": "DOSWELL TRUCK STOP",
      "address": "I-95, EXIT 98 & SR-30",
      "city": "Doswell",
      "state": "VA",
      "retail_price": 2.899,
      "distance_along_route_miles": 311.29,
      "detour_miles": 0.42,
      "coordinates": {
        "latitude": 37.8639,
        "longitude": -77.4682
      },
      "fuel_before_arrival_gallons": 18.87,
      "fuel_pumped_gallons": 3.45,
      "fuel_after_refueling_gallons": 22.32,
      "cost_dollars": 9.99
    },
    {
      "stop_index": 2,
      "opis_id": 4820,
      "truckstop_name": "SHEETZ #548",
      "address": "I-40/I-85, EXIT 132 & MT HOPE CHURCH RD",
      "city": "Greensboro",
      "state": "NC",
      "retail_price": 2.859,
      "distance_along_route_miles": 534.47,
      "detour_miles": 1.15,
      "coordinates": {
        "latitude": 36.0726,
        "longitude": -79.792
      },
      "fuel_before_arrival_gallons": 0.0,
      "fuel_pumped_gallons": 33.06,
      "fuel_after_refueling_gallons": 33.06,
      "cost_dollars": 94.52
    }
  ],
  "map_url": "/map/?start=New+York%2C+NY&finish=Atlanta%2C+GA",
  "geojson": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "geometry": { "type": "LineString", "coordinates": [[-73.9387, 40.6627], "..."] },
        "properties": { "name": "Route Path", "stroke": "#2563eb" }
      }
    ]
  },
  "execution_metrics": {
    "external_api_calls": 1,
    "geocoding_time_ms": 0.05,
    "routing_time_ms": 1545.0,
    "spatial_search_time_ms": 18.1,
    "optimization_time_ms": 12.3,
    "total_response_time_ms": 1575.5
  }
}
```

### 2. Health Check

**Endpoint**: `GET /api/health/`

```bash
curl -X GET "http://localhost:8000/api/health/"
```

```json
{
  "status": "healthy",
  "service": "Spotter Fuel Routing API",
  "version": "1.0.0",
  "indexed_stations": 6626,
  "max_vehicle_range_miles": 500,
  "default_mpg": 10
}
```

---

## 🧪 Running Tests

To run the automated unit and integration tests:

```bash
python manage.py test
```

Test coverage includes:
- **Geocoding tests**: Instant local gazetteer resolution, coordinate parsing, and error validation.
- **Spatial indexing tests**: 3D spherical nearest-neighbor queries and route corridor filtering.
- **Refueling optimizer tests**: Short route zero-stop edge cases, long-distance multi-stop schedules, and tank capacity limits.
- **API tests**: Endpoints, status codes, serializer validation, GeoJSON structures, and template rendering.

---

## 📁 Project Structure

```
spotterbackend/
├── fuel-prices-for-be-assessment.csv   # Original fuel prices dataset
├── manage.py                          # Django management CLI
├── requirements.txt                   # Production dependencies
├── README.md                          # Project documentation
├── data/
│   ├── stations.json                  # Pre-geocoded US truckstops (6,626 records)
│   ├── stations.db                    # Indexed SQLite database
│   └── cities_lookup.json             # 40,000+ US cities local geocoding cache
├── scripts/
│   ├── build_station_db.py            # Offline geocoding and index build script
│   └── test_pipeline.py               # End-to-end integration test
├── spotter_backend/
│   ├── settings.py                    # Django configuration
│   ├── urls.py                        # Root URL routing
│   └── wsgi.py                        # WSGI entrypoint
└── fuel_routing/                      # Core application
    ├── apps.py
    ├── serializers.py                 # DRF request & response serializers
    ├── views.py                       # REST API & HTML map views
    ├── urls.py                        # App URL routes
    ├── tests.py                       # Comprehensive test suite
    ├── templates/
    │   └── map.html                   # Interactive Leaflet UI template
    └── services/
        ├── geocoding.py               # Local & fallback geocoding service
        ├── osrm_client.py             # Free OSRM driving route client
        ├── spatial_index.py           # 3D cKDTree route corridor search
        └── optimizer.py               # HiGHS Linear Program fuel optimizer
```
