import math
from datetime import datetime, timezone

EARTH_RADIUS_M = 6371000.0


def _haversine_distance(lat1, lng1, lat2, lng2):
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_M * c


def check_anchor_in_restricted_zone(ship, restricted_zones):
    nav_status = ship.get("nav_status", "")
    speed = ship.get("speed", 0.0)
    is_anchored = nav_status == "at_anchor" or speed < 1.0

    if not is_anchored or not restricted_zones:
        return {"in_zone": False, "zone_id": None, "distance_to_center": None}

    ship_lat = ship["lat"]
    ship_lng = ship["lng"]

    for zone in restricted_zones:
        center_lng, center_lat = zone["center"]
        radius = zone["radius_meters"]
        dist = _haversine_distance(ship_lat, ship_lng, center_lat, center_lng)
        if dist <= radius:
            return {"in_zone": True, "zone_id": zone["zone_id"], "distance_to_center": round(dist, 2)}

    min_dist = None
    nearest_zone_id = None
    for zone in restricted_zones:
        center_lng, center_lat = zone["center"]
        dist = _haversine_distance(ship_lat, ship_lng, center_lat, center_lng)
        if min_dist is None or dist < min_dist:
            min_dist = dist
            nearest_zone_id = zone["zone_id"]

    return {"in_zone": False, "zone_id": None, "distance_to_center": round(min_dist, 2)}


def check_anchor_duration(mmsi, db):
    tracks = list(db["ship_tracks"].find({"mmsi": mmsi}).sort("timestamp", 1))

    if not tracks:
        return {"is_anchoring": False, "duration_minutes": 0.0, "zone_id": None, "anchor_damage_risk": False}

    segments = []
    seg_start = None
    seg_zone = None

    for point in tracks:
        nav_status = point.get("nav_status", "")
        speed = point.get("speed", 0.0)
        in_zone = point.get("in_restricted_zone", False)
        is_anchored = nav_status == "at_anchor" or speed < 1.0

        if is_anchored and in_zone:
            if seg_start is None:
                seg_start = point["timestamp"]
                seg_zone = point.get("zone_id")
        else:
            if seg_start is not None:
                segments.append({"start": seg_start, "end": point["timestamp"], "zone_id": seg_zone})
                seg_start = None
                seg_zone = None

    if seg_start is not None:
        segments.append({"start": seg_start, "end": datetime.now(timezone.utc), "zone_id": seg_zone})

    if not segments:
        return {"is_anchoring": False, "duration_minutes": 0.0, "zone_id": None, "anchor_damage_risk": False}

    longest = max(segments, key=lambda s: (s["end"] - s["start"]).total_seconds())
    duration_sec = (longest["end"] - longest["start"]).total_seconds()
    duration_min = duration_sec / 60.0

    return {
        "is_anchoring": True,
        "duration_minutes": round(duration_min, 2),
        "zone_id": longest["zone_id"],
        "anchor_damage_risk": duration_sec > 180,
    }


def assess_anchor_risk(ship, restricted_zones, db):
    zone_check = check_anchor_in_restricted_zone(ship, restricted_zones)
    duration_check = check_anchor_duration(ship["mmsi"], db)

    risk_score = 0.0

    if zone_check["in_zone"]:
        risk_score += 0.4
        if duration_check["is_anchoring"]:
            risk_score += min(0.3, duration_check["duration_minutes"] / 30.0)

    draught = ship.get("draught", 5.0)
    if draught > 10:
        risk_score += 0.15
    elif draught > 5:
        risk_score += 0.05

    ship_type = ship.get("ship_type", "other")
    if ship_type in ("tanker", "cargo"):
        risk_score += 0.15

    risk_score = min(1.0, risk_score)

    risk_level = "safe"
    if risk_score > 0.7:
        risk_level = "danger"
    elif risk_score > 0.5:
        risk_level = "warning"
    elif risk_score > 0.3:
        risk_level = "caution"

    return {
        "mmsi": ship.get("mmsi"),
        "in_zone": zone_check["in_zone"],
        "zone_id": zone_check.get("zone_id") or duration_check.get("zone_id"),
        "distance_to_center": zone_check["distance_to_center"],
        "is_anchoring": duration_check["is_anchoring"],
        "duration_minutes": duration_check["duration_minutes"],
        "anchor_damage_risk": duration_check["anchor_damage_risk"],
        "risk_score": round(risk_score, 4),
        "risk_level": risk_level,
    }
