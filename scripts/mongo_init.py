import random
from pymongo import MongoClient

CENTER_LNG = 121.5
CENTER_LAT = 31.0
GRID_ROWS = 8
GRID_COLS = 10
SPACING = 0.005
TURBINE_COUNT = GRID_ROWS * GRID_COLS

client = MongoClient("localhost", 27017)
db = client["windfarm_warning"]

collection_names = ["turbines", "cables", "ships", "alerts", "ship_tracks", "restricted_zones", "traffic_logs"]

for name in collection_names:
    db.drop_collection(name)
    print(f"Dropped collection: {name}")

print()

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
print(f"  Substation at ({substation['lng']}, {substation['lat']})")

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
print(f"Inserted {len(restricted_zones)} restricted zones ({len(cables)} cable_protection + {TURBINE_COUNT} turbine_safety)")

db.ships.create_index([("mmsi", 1)])
db.ships.create_index([("timestamp", -1)])
db.alerts.create_index([("level", 1)])
db.alerts.create_index([("timestamp", -1)])
db.traffic_logs.create_index([("timestamp", -1)])
print("Created indexes:")
print("  ships: mmsi, timestamp")
print("  alerts: level, timestamp")
print("  traffic_logs: timestamp")

print()
print("=== Initialization Summary ===")
print(f"Database: windfarm_warning")
print(f"Collections: {', '.join(collection_names)}")
print(f"Turbines: {len(turbines)} (80 grid + 1 substation)")
print(f"Cables: {len(cables)} ({GRID_ROWS} inter-array + 1 export)")
print(f"Restricted zones: {len(restricted_zones)} ({len(cables)} cable_protection + {TURBINE_COUNT} turbine_safety)")
print(f"Indexes created on ships, alerts, traffic_logs")
print("Done.")
