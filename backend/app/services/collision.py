import math
import os
from collections import defaultdict
from pathlib import Path

import yaml

EARTH_RADIUS_M = 6371000.0
GRID_CELL_SIZE = 0.01


class SpatialGridIndex:
    def __init__(self, cell_size=GRID_CELL_SIZE):
        self._cell_size = cell_size
        self._grid = defaultdict(list)

    def insert(self, item, lat_key="lat", lng_key="lng"):
        ci = int(item.get(lat_key, 0) / self._cell_size)
        cj = int(item.get(lng_key, 0) / self._cell_size)
        self._grid[(ci, cj)].append(item)

    def query_nearby(self, lat, lng, radius_cells=2):
        ci = int(lat / self._cell_size)
        cj = int(lng / self._cell_size)
        result = []
        for di in range(-radius_cells, radius_cells + 1):
            for dj in range(-radius_cells, radius_cells + 1):
                result.extend(self._grid.get((ci + di, cj + dj), []))
        return result

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "fuzzy_rules.yaml"


def _load_fuzzy_config(path=None):
    if path is None:
        path = _CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _latlng_to_meters(lat1, lng1, lat2, lng2):
    avg_lat_rad = math.radians((lat1 + lat2) / 2)
    x = (lng2 - lng1) * math.radians(1) * math.cos(avg_lat_rad) * EARTH_RADIUS_M
    y = (lat2 - lat1) * math.radians(1) * EARTH_RADIUS_M
    return x, y


def calculate_dcpa_tcpa(ship, target):
    ship_lat = ship["lat"]
    ship_lng = ship["lng"]
    ship_speed = ship["speed"]
    ship_course = ship["course"]
    target_lat = target["lat"]
    target_lng = target["lng"]

    dx, dy = _latlng_to_meters(ship_lat, ship_lng, target_lat, target_lng)

    course_rad = math.radians(ship_course)
    vx = ship_speed * 0.514444 * math.sin(course_rad)
    vy = ship_speed * 0.514444 * math.cos(course_rad)

    speed_sq = vx * vx + vy * vy
    if speed_sq < 1e-10:
        dcpa = math.sqrt(dx * dx + dy * dy)
        tcpa = float("inf")
        return dcpa, tcpa

    tcpa_sec = (dx * vx + dy * vy) / speed_sq
    dcpa = abs(dx * vy - dy * vx) / math.sqrt(speed_sq)

    tcpa_min = tcpa_sec / 60.0

    return dcpa, tcpa_min


def _trimf(x, a, b, c):
    if x <= a or x >= c:
        return 0.0
    if x == b:
        return 1.0
    if x < b:
        return (x - a) / (b - a) if b != a else 0.0
    return (c - x) / (c - b) if c != b else 0.0


def _trapmf(x, a, b, c, d):
    if x <= a or x >= d:
        return 0.0
    if b <= x <= c:
        return 1.0
    if x < b:
        return (x - a) / (b - a) if b != a else 0.0
    return (d - x) / (d - c) if d != c else 0.0


def _apply_mf(x, mf_def):
    mf_type = mf_def["type"]
    params = mf_def["params"]
    if mf_type == "tri":
        return _trimf(x, *params)
    elif mf_type == "trap":
        return _trapmf(x, *params)
    return 0.0


class FuzzyCollisionRisk:
    def __init__(self, config_path=None, ema_alpha=None):
        self._config = _load_fuzzy_config(config_path)
        self._dcpa_defs = self._config["dcpa"]
        self._tcpa_defs = self._config["tcpa"]
        self._ship_type_section = self._config["ship_type"]
        self._draught_defs = self._config["draught"]
        self._risk_defs = self._config["risk_output"]
        self._rules = [tuple(r) for r in self._config["rules"]]
        alpha = ema_alpha if ema_alpha is not None else self._config.get("ema_alpha", 0.3)
        self._ema_alpha = alpha
        self._ema_state = {}
        self._defuzzify_steps = self._config.get("defuzzify_steps", 200)

    def _dcpa_membership(self, dcpa):
        return {label: _apply_mf(dcpa, mf_def) for label, mf_def in self._dcpa_defs.items()}

    def _tcpa_membership(self, tcpa):
        return {label: _apply_mf(tcpa, mf_def) for label, mf_def in self._tcpa_defs.items()}

    def _ship_type_factor(self, ship_type):
        mapping = self._ship_type_section.get("mapping", {})
        return mapping.get(ship_type, mapping.get("other", 0.2))

    def _ship_type_membership(self, factor):
        defs = {k: v for k, v in self._ship_type_section.items() if k != "mapping"}
        return {label: _apply_mf(factor, mf_def) for label, mf_def in defs.items()}

    def _draught_membership(self, draught):
        return {label: _apply_mf(draught, mf_def) for label, mf_def in self._draught_defs.items()}

    def _risk_membership(self, risk_val):
        return {label: _apply_mf(risk_val, mf_def) for label, mf_def in self._risk_defs.items()}

    def evaluate(self, dcpa, tcpa, ship_type="other", draught=5.0, mmsi=None):
        dcpa_mf = self._dcpa_membership(dcpa)
        tcpa_mf = self._tcpa_membership(tcpa)
        st_factor = self._ship_type_factor(ship_type)
        st_mf = self._ship_type_membership(st_factor)
        dr_mf = self._draught_membership(draught)

        output_mf = {label: 0.0 for label in self._risk_defs}

        for dcpa_label, tcpa_label, st_label, dr_label, risk_label in self._rules:
            strength = dcpa_mf.get(dcpa_label, 0) * tcpa_mf.get(tcpa_label, 0)

            if st_label is not None:
                strength *= st_mf.get(st_label, 0)
            if dr_label is not None:
                strength *= dr_mf.get(dr_label, 0)

            output_mf[risk_label] = max(output_mf.get(risk_label, 0), strength)

        raw_score = self._defuzzify(output_mf)

        smoothed_score = self._apply_ema(raw_score, mmsi)

        return smoothed_score

    def _apply_ema(self, raw_score, mmsi=None):
        if mmsi is None:
            return raw_score
        alpha = self._ema_alpha
        if mmsi in self._ema_state:
            prev = self._ema_state[mmsi]
            smoothed = alpha * raw_score + (1 - alpha) * prev
        else:
            smoothed = raw_score
        self._ema_state[mmsi] = smoothed
        return smoothed

    def _defuzzify(self, output_mf, steps=None):
        if steps is None:
            steps = self._defuzzify_steps
        x_min, x_max = 0.0, 1.0
        step_size = (x_max - x_min) / steps
        numerator = 0.0
        denominator = 0.0

        for i in range(steps + 1):
            x = x_min + i * step_size
            risk_mf = self._risk_membership(x)
            clipped = {}
            for label, degree in output_mf.items():
                clipped[label] = min(degree, risk_mf.get(label, 0))
            agg = max(clipped.values())
            numerator += x * agg
            denominator += agg

        if denominator < 1e-10:
            return 0.0
        return numerator / denominator

    def reset_ema(self, mmsi=None):
        if mmsi is None:
            self._ema_state.clear()
        else:
            self._ema_state.pop(mmsi, None)


def _load_hysteresis(config_path=None):
    config = _load_fuzzy_config(config_path)
    return config.get("hysteresis", {
        "safe": {"up": 0.30, "down": 0.20},
        "caution": {"up": 0.50, "down": 0.35},
        "warning": {"up": 0.70, "down": 0.55},
        "danger": {"up": 0.85, "down": 0.65},
    })


_HYSTERESIS_THRESHOLDS = _load_hysteresis()

_LEVEL_ORDER = ["safe", "caution", "warning", "danger"]
_LEVEL_RANK = {lv: i for i, lv in enumerate(_LEVEL_ORDER)}

_previous_levels = {}


def _hysteresis_level(score, mmsi=None):
    if mmsi is None:
        return _score_to_level(score)

    prev = _previous_levels.get(mmsi, "safe")
    prev_rank = _LEVEL_RANK[prev]

    new_level = prev
    if score >= _HYSTERESIS_THRESHOLDS["danger"]["up"]:
        new_level = "danger"
    elif score >= _HYSTERESIS_THRESHOLDS["warning"]["up"] and prev_rank >= _LEVEL_RANK["warning"]:
        new_level = "warning"
    elif score >= _HYSTERESIS_THRESHOLDS["warning"]["up"] and prev_rank < _LEVEL_RANK["warning"]:
        new_level = "warning"
    elif score <= _HYSTERESIS_THRESHOLDS["safe"]["down"]:
        new_level = "safe"
    elif score <= _HYSTERESIS_THRESHOLDS["caution"]["down"] and prev_rank > _LEVEL_RANK["caution"]:
        new_level = "caution"
    elif score <= _HYSTERESIS_THRESHOLDS["warning"]["down"] and prev_rank > _LEVEL_RANK["warning"]:
        new_level = "warning"

    if new_level == prev:
        if prev_rank < _LEVEL_RANK["danger"] and score >= _HYSTERESIS_THRESHOLDS[_LEVEL_ORDER[prev_rank + 1]]["up"]:
            new_level = _LEVEL_ORDER[prev_rank + 1]
        elif prev_rank > 0 and score <= _HYSTERESIS_THRESHOLDS[_LEVEL_ORDER[prev_rank]]["down"]:
            new_level = _LEVEL_ORDER[prev_rank - 1]

    _previous_levels[mmsi] = new_level
    return new_level


def _score_to_level(score):
    if score > 0.7:
        return "danger"
    elif score > 0.5:
        return "warning"
    elif score > 0.3:
        return "caution"
    return "safe"


def _haversine_m(lat1, lng1, lat2, lng2):
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def _point_in_polygon(lat, lng, polygon):
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lng_i = polygon[i]
        lat_j, lng_j = polygon[j]
        if ((lat_i > lat) != (lat_j > lat)) and (
            lng < (lng_j - lng_i) * (lat - lat_i) / (lat_j - lat_i) + lng_i
        ):
            inside = not inside
        j = i
    return inside


def _is_in_restricted_zone(ship_lat, ship_lng, restricted_zones):
    for zone in restricted_zones:
        polygon = zone.get("polygon", [])
        if polygon and _point_in_polygon(ship_lat, ship_lng, polygon):
            return True
        center = zone.get("center")
        if center and len(center) >= 2:
            center_lng, center_lat = center[0], center[1]
            radius = zone.get("radius_meters", zone.get("radius", 500))
            dist = _haversine_m(ship_lat, ship_lng, center_lat, center_lng)
            if dist <= radius:
                return True
    return False


_fuzzy_instance = FuzzyCollisionRisk()


def _load_collision_warning_config(config_path=None):
    config = _load_fuzzy_config(config_path)
    return config.get("collision_warning", {
        "dcpa_threshold": 500,
        "tcpa_threshold": 10,
        "risk_score_threshold": 0.7,
    })


_COLLISION_WARNING_CFG = _load_collision_warning_config()


def assess_collision_risk(ship, turbines, restricted_zones=None):
    if restricted_zones is None:
        restricted_zones = []

    ship_type = ship.get("ship_type", "other")
    draught = ship.get("draught", 5.0)
    mmsi = ship.get("mmsi")

    min_dcpa = float("inf")
    min_tcpa = float("inf")
    nearest_turbine_id = None
    in_restricted = False
    est_entry = None

    for turbine in turbines:
        target = {"lat": turbine["lat"], "lng": turbine["lng"]}
        dcpa, tcpa = calculate_dcpa_tcpa(ship, target)
        if dcpa < min_dcpa:
            min_dcpa = dcpa
            min_tcpa = tcpa
            nearest_turbine_id = turbine.get("id", turbine.get("turbine_id"))

    for zone in restricted_zones:
        polygon = zone.get("polygon", [])
        if polygon and _point_in_polygon(ship["lat"], ship["lng"], polygon):
            in_restricted = True
            if min_tcpa != float("inf") and min_tcpa > 0:
                est_entry = 0.0
            break

    if not in_restricted:
        in_restricted = _is_in_restricted_zone(ship["lat"], ship["lng"], restricted_zones)
        if in_restricted:
            est_entry = 0.0

    if min_dcpa == float("inf"):
        min_dcpa = 0.0
    if min_tcpa == float("inf"):
        min_tcpa = 0.0

    risk_score = _fuzzy_instance.evaluate(min_dcpa, min_tcpa, ship_type, draught, mmsi=mmsi)

    risk_level = _hysteresis_level(risk_score, mmsi=mmsi)

    if in_restricted:
        risk_score = min(1.0, risk_score + 0.2)
        if _LEVEL_RANK[risk_level] < _LEVEL_RANK["warning"]:
            risk_level = "warning"

    if est_entry is None and min_tcpa > 0:
        est_entry = round(min_tcpa, 2)
    elif est_entry is None:
        est_entry = None

    return {
        "mmsi": mmsi,
        "risk_score": round(risk_score, 4),
        "risk_level": risk_level,
        "dcpa": round(min_dcpa, 2),
        "tcpa": round(min_tcpa, 2),
        "nearest_turbine_id": nearest_turbine_id,
        "in_restricted_zone": in_restricted,
        "estimated_entry_time": est_entry,
    }


def check_collision_warning(risk_assessment):
    dcpa_thr = _COLLISION_WARNING_CFG["dcpa_threshold"]
    tcpa_thr = _COLLISION_WARNING_CFG["tcpa_threshold"]
    score_thr = _COLLISION_WARNING_CFG["risk_score_threshold"]
    if risk_assessment["dcpa"] < dcpa_thr and risk_assessment["tcpa"] < tcpa_thr:
        return True
    if risk_assessment["risk_score"] > score_thr:
        return True
    return False
