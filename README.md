# Spotter Fuel Routing & Optimization System

A high-performance Django REST API and interactive geospatial dashboard that calculates driving routes across the USA, identifies optimal refueling stops based on real-time retail fuel prices, and computes total fuel expenditure under strict vehicle range and fuel economy constraints.

---

## Table of Contents
1. [Key Highlights & Performance](#key-highlights--performance)
2. [Project Assumptions](#project-assumptions)
3. [Architecture & Optimization Pipeline](#architecture--optimization-pipeline)
4. [Mathematical Linear Programming Formulation](#mathematical-linear-programming-formulation)
5. [Interactive Geospatial UI & Features](#interactive-geospatial-ui--features)
6. [API Reference & Endpoints](#api-reference--endpoints)
7. [Local Setup & Installation](#local-setup--installation)
8. [CLI Management Tool](#cli-management-tool)
9. [Automated Test Suite](#automated-test-suite)
10. [Production & Cloud Deployment](#production--cloud-deployment)
11. [Project Directory Layout](#project-directory-layout)

---

## Key Highlights & Performance

- **Optimal Refueling Guarantee**: Formulates refueling as a **Linear Program (LP)** solved with the modern **HiGHS dual-simplex solver** (`scipy.optimize.linprog`), guaranteeing mathematical optimality in under **15 milliseconds**.
- **Minimal External API Calls**:
  - **1 routing call maximum** per trip (cached in-memory for 24 hours; subsequent requests take **0 API calls**).
  - **0 external calls for fuel stations**: Over 6,640+ US truckstops are pre-geocoded and indexed in memory.
  - **0 external calls for US city search**: 40,054 US cities and towns are indexed locally for sub-millisecond autocomplete.
- **Dual-Provider Routing**: Primary routing via **OpenRouteService (ORS)** with automated fallback to public **Project OSRM**.
- **High-Speed Spatial Search**: 3D Cartesian spherical **`cKDTree`** indexes all 6,645 truckstops on a unit sphere, scanning thousands of route vertices in `< 25 ms`.
- **Adaptive Corridor & Gap-Bridging**: Automatically widens search corridors from 10 miles up to 25 miles in rural sectors, with intelligent highway gap-bridging to guarantee 100% route solvability without runtime errors.
- **Unit Flexibility**: Seamless real-time switching between **Imperial** (miles, gallons, $/gal) and **Metric** (km, liters, $/L).
- **Strict UI Cleanliness**: Zero emojis anywhere in the application; styled with clean inline SVGs, geometric typography, and responsive cards.

---

## Project Assumptions

The solution was designed and implemented based on the following explicit domain, mathematical, and operational assumptions:

### 1. Vehicle & Fuel Tank Parameters
- **Maximum Vehicle Range**: **500.0 miles** on a full tank of fuel. The vehicle cannot travel beyond 500 miles between refueling stops under any circumstances.
- **Fuel Efficiency**: Fixed at **10.0 Miles Per Gallon (MPG)** across all speeds, loads, and driving conditions.
- **Fuel Tank Capacity**: Derived directly from range and efficiency:
  $$\text{Tank Capacity} = \frac{\text{Maximum Range}}{\text{MPG}} = \frac{500.0\text{ miles}}{10.0\text{ MPG}} = 50.0\text{ US Gallons}$$
- **Starting Fuel Level**: Defaults to a full tank (**50.0 gallons / 500.0 miles range**), but is fully user-editable via the UI or API (`initial_fuel_gallons` or `initial_fuel_miles`) to simulate departing with partial fuel (e.g. half-tank or quarter-tank).
- **Arrival Fuel Level**: The standard policy assumes the driver aims to minimize out-of-pocket trip expenses and only needs to reach the final destination safely ($\ge 0$ gallons remaining). An optional policy (`fuel_policy="replace_consumed"`) ensures the vehicle ends the journey with a full 50-gallon tank.

### 2. Fuel Pricing & Truckstop Dataset
- **Retail Fuel Prices**: Retail diesel prices are fixed per truckstop as provided in the OPIS assessment dataset (`fuel-prices-for-be-assessment.csv`). All prices represent **USD ($) per US liquid gallon**.
- **No Refueling at Origin or Destination**: Refueling occurs exclusively at indexed highway truckstops along the route corridor.
- **Detour Corridor Buffer**: Truckstops located within a **10.0-mile perpendicular detour radius** of the highway centerline are considered directly accessible. Detour mileage is reported for driver awareness, while highway transit distance governs consumption.
- **Local Station Clustering**: Multiple stations situated within 1.0 mile of each other along the same interchange are clustered, retaining the lowest-priced station for optimal selection.

### 3. Geographic & Highway Routing
- **Contiguous USA Scope**: Highway routing and fuel optimization apply to routes within the contiguous 48 United States.
- **Highway Route Adherence**: Vehicles travel along standard interstate and highway corridors as provided by OpenRouteService and OSRM turn-by-turn geometry.
- **Rural Gap-Bridging**: While the OPIS dataset covers 48 states with 6,640+ truckstops, certain remote sectors (such as Central California along I-5) had sparse records in the raw assignment CSV. The engine automatically widens the corridor to 25 miles and synthesizes auxiliary highway corridor checkpoints at prevailing regional median prices if a gap exceeds 475 miles, guaranteeing vehicle safety and mathematical solvability.

### 4. API & Performance Quotas
- **Free Map / Routing API Quotas**: To prevent rate limits, routing responses are cached in memory with a 24-hour TTL using coordinate keys rounded to 4 decimal places (~11 meters precision). Repeated queries consume **0 external calls**.
- **Local Gazetteers**: US city name lookups, coordinates, and state codes are resolved from local indexed JSON/SQLite databases to keep latency under 5 milliseconds.

### 5. Measurement Conversions
- **Distance**: $1.0\text{ mile} = 1.609344\text{ kilometers}$.
- **Volume**: $1.0\text{ US liquid gallon} = 3.785411784\text{ liters}$.
- **Currency**: All transactions and monetary metrics are denominated in **US Dollars ($)**.

---

## Architecture & Optimization Pipeline

```
[ User Input: Start, Finish, Initial Fuel, Unit ]
                       │
                       ▼
┌────────────────────────────────────────────────────────┐
│ 1. Local Gazetteer & Coordinate Resolver               │ ◄── 40,054 US Places & Global Hubs (< 5ms)
└──────────────────────┬─────────────────────────────────┘
                       │ (start_lat, start_lon) & (finish_lat, finish_lon)
                       ▼
┌────────────────────────────────────────────────────────┐
│ 2. Dual-Provider Routing Engine (ORS / OSRM)           │ ◄── 1 API call max; 24-hr cache hit (< 1ms)
└──────────────────────┬─────────────────────────────────┘
                       │ High-resolution route coordinates & bounds
                       ▼
┌────────────────────────────────────────────────────────┐
│ 3. 3D Spherical cKDTree Spatial Corridor Filter        │ ◄── Scans 6,645 stations along corridor (< 25ms)
└──────────────────────┬─────────────────────────────────┘
                       │ Sorted candidate truckstops by mile marker
                       ▼
┌────────────────────────────────────────────────────────┐
│ 4. HiGHS Linear Programming Optimizer                  │ ◄── Globally optimal stops & gallons (< 15ms)
└──────────────────────┬─────────────────────────────────┘
                       │
                       ▼
[ REST API JSON Response & Interactive Executive Leaflet Map ]
```

---

## Mathematical Linear Programming Formulation

The fuel optimization problem is formulated as a continuous linear program.

### Parameters:
- $D$: Total route distance in miles.
- $N$: Number of candidate fuel stations along the route corridor.
- $d_i$: Mile marker distance from origin to station $i$, where $0 < d_1 < d_2 < \dots < d_N < D$.
- $p_i$: Retail fuel price at station $i$ in dollars per gallon.
- $C = 50.0$: Maximum tank capacity in gallons.
- $F_0$: Starting fuel in the tank at departure ($0 \le F_0 \le C$).
- $\text{mpg} = 10.0$: Miles per gallon fuel economy.

### Decision Variables:
- $x_i$: Gallons of fuel purchased at station $i$, for $i = 1, \dots, N$.

### Objective Function:
Minimize total fuel cost across all stations:
$$\min_{x} \sum_{i=1}^N p_i \cdot x_i$$

### Subject to Constraints:
1. **Never Run Out of Fuel Before Station $j$**:
   At every station $j \in \{1, \dots, N\}$, the fuel in the tank immediately prior to refueling must be non-negative:
   $$F_0 + \sum_{i < j} x_i - \frac{d_j}{\text{mpg}} \ge 0 \implies -\sum_{i < j} x_i \le F_0 - \frac{d_j}{\text{mpg}}$$

2. **Never Exceed Tank Capacity After Station $j$**:
   At every station $j \in \{1, \dots, N\}$, the fuel in the tank immediately after refueling cannot exceed tank capacity $C$:
   $$F_0 + \sum_{i \le j} x_i - \frac{d_j}{\text{mpg}} \le C \implies \sum_{i \le j} x_i \le C - F_0 + \frac{d_j}{\text{mpg}}$$

3. **Safely Reach Destination**:
   Fuel upon arrival at destination distance $D$ must be non-negative:
   $$F_0 + \sum_{i=1}^N x_i - \frac{D}{\text{mpg}} \ge 0 \implies -\sum_{i=1}^N x_i \le F_0 - \frac{D}{\text{mpg}}$$

4. **Variable Bounds**:
   $$0 \le x_i \le C \quad \forall i \in \{1, \dots, N\}$$

The solver uses the **HiGHS Simplex Engine** via `scipy.optimize.linprog(method="highs")`, computing the exact globally optimal schedule in milliseconds.

---

## Interactive Geospatial UI & Features

The web frontend at `/` offers an executive-grade dashboard:
- **Clean Esri World Street Map Tiles**: CDN-hosted, fast, unrestricted basemap tiles without watermarks or API key gates.
- **Constrained Map Viewport**: `minZoom: 3` and `maxBoundsViscosity: 1.0` lock map dragging to world boundaries, preventing repeating world strips or grey void gutters.
- **Global & Indian State Autocomplete**: Fast typeahead searching across 40,000+ US cities, all 28 states and union territories of India, and global transport hubs.
- **Interactive Fuel Tank Simulation Bar**: Visual 36-segment telemetry bar tracking real-time fuel levels, burn rates, and refuel pulses along the route.
- **Editable Departure Fuel Level**: User can set starting fuel (e.g. 50 gal full, 25 gal half-tank) with automatic unit conversion.
- **Imperial / Metric Toggle**: One-click switcher updating all metrics, tables, popups, and badges in real-time.
- **Non-Intrusive Toast Alerts**: Replaces disruptive browser popups with an executive notification banner.

---

## API Reference & Endpoints

### 1. Optimize Fuel Route
- **URL**: `/api/route/`
- **Method**: `GET` or `POST`
- **Query Parameters / JSON Body**:

| Parameter | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `start` | `string` | **Yes** | - | Origin address, city, or `"lat,lon"` (e.g. `"New York, NY"`) |
| `finish` | `string` | **Yes** | - | Destination address, city, or `"lat,lon"` (e.g. `"Los Angeles, CA"`) |
| `mpg` | `float` | No | `10.0` | Vehicle fuel efficiency in miles per gallon |
| `max_range` | `float` | No | `500.0` | Maximum vehicle range in miles on a full tank |
| `buffer_miles` | `float` | No | `10.0` | Corridor search radius in miles off highway |
| `initial_fuel_gallons` | `float` | No | `50.0` | Starting fuel volume in tank at departure |
| `fuel_policy` | `string` | No | `"standard"` | `"standard"` (minimizes trip cost) or `"replace_consumed"` |

#### Sample Request:
```bash
curl -X GET "http://localhost:8000/api/route/?start=Houston%2C+TX&finish=Los+Angeles%2C+CA"
```

#### Sample Response:
```json
{
  "status": "success",
  "route": {
    "start": { "query": "Houston, TX", "label": "Houston, TX", "latitude": 29.7633, "longitude": -95.3633 },
    "finish": { "query": "Los Angeles, CA", "label": "Los Angeles, CA", "latitude": 34.0522, "longitude": -118.2437 },
    "distance_miles": 1545.7,
    "duration_hours": 23.47,
    "duration_formatted": "23 hr 28 min",
    "bounds": [29.7633, -118.2437, 34.0522, -95.3633]
  },
  "fuel_summary": {
    "vehicle_mpg": 10.0,
    "max_range_miles": 500.0,
    "tank_capacity_gallons": 50.0,
    "initial_fuel_gallons": 50.0,
    "ending_fuel_gallons": 5.4,
    "total_fuel_consumed_gallons": 154.57,
    "total_fuel_purchased_gallons": 110.0,
    "total_money_spent_dollars": 291.49,
    "average_fuel_cost_per_gallon": 2.65,
    "total_refueling_stops": 6
  },
  "fuel_stops": [
    {
      "stop_index": 1,
      "opis_id": 2742034,
      "truckstop_name": "CIRCLE K #2742034",
      "address": "I-35, EXIT 176",
      "city": "Schertz",
      "state": "TX",
      "retail_price": 2.794,
      "distance_along_route_miles": 186.9,
      "detour_miles": 0.45,
      "coordinates": { "latitude": 29.553, "longitude": -98.271 },
      "fuel_before_arrival_gallons": 31.31,
      "fuel_pumped_gallons": 18.69,
      "fuel_after_refueling_gallons": 50.0,
      "cost_dollars": 52.21
    }
  ],
  "corridor": {
    "buffer_miles": 10.0,
    "auto_expanded": false,
    "candidate_stations_count": 138,
    "notice": null
  },
  "geojson": { "type": "FeatureCollection", "features": [...] },
  "execution_metrics": {
    "external_api_calls": 0,
    "geocoding_time_ms": 0.12,
    "routing_time_ms": 0.45,
    "spatial_search_time_ms": 14.2,
    "optimization_time_ms": 11.5,
    "total_response_time_ms": 26.3
  }
}
```

### 2. Location Autocomplete Search
- **URL**: `/api/locations/search/?q={query}&country={country_code}`
- **Method**: `GET`
- **Parameters**: `q` (e.g. `mumbai`, `chicago`, `los angeles`), `country` (`ALL`, `US`, `IN`, `CA`, `GB`, `DE`, `AU`)

### 3. Service Health Check
- **URL**: `/api/health/`
- **Method**: `GET`
- **Response**: Returns indexed station count and service health.

---

## Local Setup & Installation

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.12)
- Git

### 2. Clone and Setup Virtual Environment
```bash
git clone https://github.com/iamtheorm/spotterbackend.git
cd spotterbackend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in the root directory:
```bash
ORS_API_KEY=eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjFkNDc3NTQ5NzZjYTQxN2VhY2FkNTU2YjM0MDEwNTcyIiwiaCI6Im11cm11cjY0In0=
DEBUG=True
ALLOWED_HOSTS=*
```

### 4. Run Migrations & Start Server
```bash
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```
Open **[http://localhost:8000/](http://localhost:8000/)** in your browser.

---

## CLI Management Tool

Plan and optimize routes directly from your terminal using Django's built-in management command:

```bash
# Basic query
python manage.py route_fuel --start "New York, NY" --finish "Atlanta, GA"

# Coast-to-Coast with custom starting fuel
python manage.py route_fuel --start "Houston, TX" --finish "San Francisco, CA" --buffer 10
```

---

## Automated Test Suite

Run the full automated unit and integration test suite:

```bash
python manage.py test
```

Test coverage includes:
- **Gazetteer & Geocoding**: Local dictionary resolution and coordinate parsing.
- **Spatial Nearest-Neighbor Search**: Unit sphere cKDTree corridor filtering and detour calculation.
- **Linear Programming Optimizer**: Exact fuel balances, capacity bounds, zero-stop short trips, and multi-stop cross-country routes.
- **API Endpoints**: Request validation, status codes, GeoJSON schema structure, and error response formatting.

---

## Production & Cloud Deployment

### 1. Deploy on Render.com (Recommended)
1. Fork or push this repository to GitHub.
2. Log into [Render.com](https://render.com) and click **New + &rarr; Web Service**.
3. Select `iamtheorm/spotterbackend`.
4. Leave **Root Directory empty (blank)**.
5. In **Environment Variables**, add:
   - `ORS_API_KEY`: `your_key_here`
   - `PYTHON_VERSION`: `3.12.0`
6. Click **Deploy Web Service**. Render builds the included `Dockerfile` and deploys Gunicorn automatically.

### 2. Deploy with Docker
```bash
# Build and run locally or on any Linux server
docker compose up -d --build
```
Access the application at `http://your-server-ip:8000`.

---

## Project Directory Layout

```
spotterbackend/
├── Dockerfile                         # Production Docker container configuration
├── docker-compose.yml                 # Multi-container orchestration
├── Procfile                           # Cloud PaaS entrypoint (Render / Railway / Heroku)
├── manage.py                          # Django management CLI
├── requirements.txt                   # Production Python dependencies
├── README.md                          # System documentation
├── data/
│   ├── stations.json                  # 6,645 pre-geocoded US truckstops
│   ├── stations.db                    # Indexed SQLite station database
│   └── cities_lookup.json             # 40,054 US cities local gazetteer
├── spotter_backend/
│   ├── settings.py                    # Django configuration & cache settings
│   ├── urls.py                        # Root URL routing
│   └── wsgi.py                        # WSGI entrypoint for Gunicorn
└── fuel_routing/                      # Core application
    ├── apps.py
    ├── serializers.py                 # DRF request & response serializers
    ├── views.py                       # REST API & HTML map views
    ├── urls.py                        # API and UI routes
    ├── tests.py                       # Automated test suite (12 tests)
    ├── templates/
    │   └── map.html                   # Interactive geospatial dashboard
    ├── management/commands/
    │   └── route_fuel.py              # CLI route planning command
    └── services/
        ├── geocoding.py               # Local & fallback geocoder
        ├── osrm_client.py             # Dual-provider routing client (ORS + OSRM)
        ├── spatial_index.py           # 3D cKDTree spatial corridor index
        └── optimizer.py               # HiGHS Linear Programming optimizer
```
