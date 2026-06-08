#!/usr/bin/env python3
import random
import time
from pymongo import MongoClient

CENTER_LNG = 121.5
CENTER_LAT = 31.0
GRID_ROWS = 8
GRID_COLS = 10
SPACING = 0.005
TURBINE_COUNT = GRID_ROWS * GRID_COLS

MONGO_HOST = "mongodb"
MONGO_PORT = 27017

max_retries = 30
client = None
for i in range(max_retries):
    try:
        client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=2000)
        client.server_info()
        print(f"Connected to MongoDB at {MONGO_HOST}:{MONGO_PORT}")
        break
    except Exception:
        print(f"Waiting for MongoDB... attempt {i + 1}/{max_retries}")
        time.sleep(2)

if client is None:
    print("Failed to connect to MongoDB, exiting")
    exit(1)

db = client["windfarm_warning"]

collection_names = ["turbines", "cables", "ships", "alerts", "ship_tracks", "restricted_zones", "traffic_logs", "pending_pushes", "risk_assessments"]

for name in collection_names:
    db.drop_collection(name)
    print(f"Dropped collection: {name}")

turbines = []
substation_lng = CENTER_LNG + (GRID_COLS - 1) * SPACING / 2
substation_lat = CENTER_LAT + (GRID_ROWS - 1) * SPACING / 2

for row in range(GRID_ROWS):
    for col in range(GRID_COLS):
        idx = row * GRID_COLS + col
        turbine_id = f"WT{idx + 1:02d}"
        lng = round(CENTER_LNG + col * SPACING, 6)
        lat = round(CENTER_LAT + row * SPACING, 6)
        turbines.append({
            "turbine_id": turbine_id,
            "lat": lat,
            "lng": lng,
            "status": "active",
            "scour_depth": round(random.uniform(0.5, 3.0), 2),
        })

substation = {
    "turbine_id": "SUB01",
    "lat": round(substation_lat, 6),
    "lng": round(substation_lng, 6),
    "status": "active",
    "scour_depth": 0.0,
    "is_substation": True,
}
turbines.append(substation)

db.turbines.insert_many(turbines)
print(f"Inserted {len(turbines)} turbines (80 grid + 1 substation)")

cables = []
cable_idx = 0

for row in range(GRID_ROWS):
    points = []
    for col in range(GRID_COLS):
        idx = row * GRID_COLS + col
        lng = round(CENTER_LNG + col * SPACING, 6)
        lat = round(CENTER_LAT + row * SPACING, 6)
        points.append([lng, lat])
    points.append([substation_lng, substation_lat])
    cable_idx += 1
    cables.append({
        "route_id": f"CAB-IA-{cable_idx:03d}",
        "points": points,
        "type": "inter-array",
        "status": "active",
    })

shore_lng = round(CENTER_LNG - 0.08, 6)
shore_lat = round(CENTER_LAT, 6)
cable_idx += 1
cables.append({
    "route_id": f"CAB-EX-{cable_idx:03d}",
    "points": [
        [substation_lng, substation_lat],
        [round(substation_lng - 0.02, 6), round(substation_lat + 0.01, 6)],
        [round(substation_lng - 0.05, 6), round(substation_lat + 0.005, 6)],
        [shore_lng, shore_lat],
    ],
    "type": "export",
    "status": "active",
})

db.cables.insert_many(cables)
print(f"Inserted {len(cables)} cable routes ({GRID_ROWS} inter-array + 1 export)")

restricted_zones = []
zone_idx = 0

for cable in cables:
    mid = len(cable["points"]) // 2
    center = cable["points"][mid]
    zone_idx += 1
    restricted_zones.append({
        "zone_id": f"ZC-{zone_idx:03d}",
        "center": center,
        "radius_meters": 500,
        "type": "cable_protection",
    })

for t in turbines:
    if t.get("is_substation"):
        continue
    zone_idx += 1
    restricted_zones.append({
        "zone_id": f"ZT-{zone_idx:03d}",
        "center": [t["lng"], t["lat"]],
        "radius_meters": 500,
        "type": "turbine_safety",
    })

db.restricted_zones.insert_many(restricted_zones)
print(f"Inserted {len(restricted_zones)} restricted zones")

print("Creating indexes...")

db.turbines.create_index([("turbine_id", 1)], unique=True)
db.turbines.create_index([("lat", 1), ("lng", 1)])
print("  turbines: turbine_id(unique), lat+lng")

db.ships.create_index([("mmsi", 1)], unique=True)
db.ships.create_index([("timestamp", -1)])
db.ships.create_index([("position", "2dsphere")])
print("  ships: mmsi(unique), timestamp, 2dsphere")

db.ship_tracks.create_index([("mmsi", 1), ("timestamp", -1)])
db.ship_tracks.create_index([("timestamp", -1)])
db.ship_tracks.create_index([("position", "2dsphere")])
print("  ship_tracks: mmsi+timestamp, timestamp, 2dsphere")

db.risk_assessments.create_index([("mmsi", 1)], unique=True)
db.risk_assessments.create_index([("risk_level", 1)])
db.risk_assessments.create_index([("timestamp", -1)])
print("  risk_assessments: mmsi(unique), risk_level, timestamp")

db.alerts.create_index([("level", 1)])
db.alerts.create_index([("timestamp", -1)])
db.alerts.create_index([("mmsi", 1), ("level", 1), ("timestamp", -1)])
print("  alerts: level, timestamp, mmsi+level+timestamp")

db.traffic_logs.create_index([("timestamp", -1)])
db.traffic_logs.create_index([("source", 1)])
print("  traffic_logs: timestamp, source")

db.restricted_zones.create_index([("zone_id", 1)], unique=True)
db.restricted_zones.create_index([("type", 1)])
db.restricted_zones.create_index([("center", "2dsphere")])
print("  restricted_zones: zone_id(unique), type, 2dsphere")

db.pending_pushes.create_index([("status", 1), ("next_retry_at", 1)])
db.pending_pushes.create_index([("alert_id", 1)])
print("  pending_pushes: status+next_retry_at, alert_id")

print()
print("Enabling sharding on windfarm_warning database...")

try:
    admin_db = client["admin"]
    admin_db.command("enableSharding", "windfarm_warning")
    print("  Sharding enabled for windfarm_warning")

    admin_db.command("shardCollection", "windfarm_warning.ships", key={"mmsi": 1})
    print("  Sharded collection: ships (key: mmsi)")

    admin_db.command("shardCollection", "windfarm_warning.ship_tracks", key={"mmsi": 1})
    print("  Sharded collection: ship_tracks (key: mmsi)")

    admin_db.command("shardCollection", "windfarm_warning.alerts", key={"timestamp": 1})
    print("  Sharded collection: alerts (key: timestamp)")

    admin_db.command("shardCollection", "windfarm_warning.traffic_logs", key={"timestamp": 1})
    print("  Sharded collection: traffic_logs (key: timestamp)")
except Exception as e:
    print(f"  Sharding skipped (requires replica set / config server): {e}")
    print("  For production, run MongoDB as replica set with config server for sharding support")

print()
print("=== Initialization Summary ===")
print(f"Database: windfarm_warning")
print(f"Collections: {len(collection_names)}")
print(f"Turbines: {len(turbines)} | Cables: {len(cables)} | Zones: {len(restricted_zones)}")
print(f"Geospatial indexes: ships, ship_tracks, restricted_zones (2dsphere)")
print(f"Sharding: ships(mmsi), ship_tracks(mmsi), alerts(timestamp), traffic_logs(timestamp)")
print("Done.")
