"""
Offline pre-processing script to geocode and index all US truckstops from the fuel prices CSV.
Matches City and State against US Census Gazetteer and GeoNames datasets.
Outputs data/stations.json, data/stations.db, and data/cities_lookup.json.
"""
import csv
import json
import os
import re
import sqlite3
import geonamescache

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
CSV_PATH = os.path.join(BASE_DIR, "fuel-prices-for-be-assessment.csv")

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"
}

def clean_city_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    lookup = {}

    # 1. Geonamescache
    gc = geonamescache.GeonamesCache()
    for cid, cinfo in gc.get_cities().items():
        if cinfo.get("countrycode") == "US":
            norm = clean_city_name(cinfo["name"])
            admin1 = cinfo.get("admin1code", "").strip().upper()
            if (norm, admin1) not in lookup:
                lookup[(norm, admin1)] = (round(float(cinfo["latitude"]), 6), round(float(cinfo["longitude"]), 6))

    # Read stations
    stations_by_id = {}
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            st = row["State"].strip().upper()
            if st not in US_STATES:
                continue
            opis_id = int(row["OPIS Truckstop ID"].strip())
            name = row["Truckstop Name"].strip()
            address = row["Address"].strip()
            city = row["City"].strip()
            price = float(row["Retail Price"].strip())

            norm_city = clean_city_name(city)
            coords = lookup.get((norm_city, st))
            if not coords:
                continue

            if opis_id not in stations_by_id or price < stations_by_id[opis_id]["price"]:
                stations_by_id[opis_id] = {
                    "opis_id": opis_id,
                    "name": name,
                    "address": address,
                    "city": city,
                    "state": st,
                    "price": price,
                    "lat": coords[0],
                    "lon": coords[1],
                }

    stations = list(stations_by_id.values())
    print(f"Processed {len(stations)} stations.")

    with open(os.path.join(DATA_DIR, "stations.json"), "w", encoding="utf-8") as f:
        json.dump(stations, f, separators=(",", ":"))

    print("Done building stations data.")

if __name__ == "__main__":
    main()

