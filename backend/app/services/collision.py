import math

EARTH_RADIUS_M = 6371000.0


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


class FuzzyCollisionRisk:
    def __init__(self, ema_alpha=0.3):
        self.rules = self._build_rules()
        self._ema_alpha = ema_alpha
        self._ema_state = {}

    def _dcpa_membership(self, dcpa):
        return {
            "very_close": _trapmf(dcpa, 0, 0, 250, 400),
            "close": _trimf(dcpa, 150, 500, 1000),
            "moderate": _trimf(dcpa, 600, 1200, 2000),
            "far": _trapmf(dcpa, 1200, 3000, 5000, 5000),
        }

    def _tcpa_membership(self, tcpa):
        return {
            "imminent": _trapmf(tcpa, 0, 0, 4, 7),
            "short": _trimf(tcpa, 2, 8, 16),
            "medium": _trimf(tcpa, 10, 20, 35),
            "long": _trapmf(tcpa, 25, 60, 120, 120),
        }

    def _ship_type_factor(self, ship_type):
        mapping = {
            "tanker": 0.9,
            "passenger": 0.8,
            "cargo": 0.6,
            "fishing": 0.4,
            "tug": 0.3,
        }
        return mapping.get(ship_type, 0.2)

    def _ship_type_membership(self, factor):
        return {
            "high_risk": _trapmf(factor, 0.6, 0.8, 1.0, 1.0),
            "medium_risk": _trimf(factor, 0.3, 0.55, 0.8),
            "low_risk": _trapmf(factor, 0.0, 0.0, 0.3, 0.55),
        }

    def _draught_membership(self, draught):
        return {
            "deep": _trapmf(draught, 8, 12, 30, 30),
            "medium": _trimf(draught, 3, 7, 12),
            "shallow": _trapmf(draught, 0, 0, 4, 7),
        }

    def _risk_membership(self, risk_val):
        return {
            "safe": _trapmf(risk_val, 0, 0, 0.15, 0.3),
            "caution": _trimf(risk_val, 0.2, 0.4, 0.6),
            "warning": _trimf(risk_val, 0.5, 0.65, 0.8),
            "danger": _trapmf(risk_val, 0.7, 0.85, 1.0, 1.0),
        }

    def _build_rules(self):
        return [
            ("very_close", "imminent", "high_risk", "deep", "danger"),
            ("very_close", "imminent", None, None, "danger"),
            ("very_close", "short", "high_risk", None, "danger"),
            ("very_close", "short", None, None, "danger"),
            ("close", "imminent", "high_risk", "deep", "danger"),
            ("close", "imminent", None, None, "danger"),
            ("close", "short", "high_risk", None, "warning"),
            ("close", "short", None, None, "warning"),
            ("moderate", "imminent", "high_risk", None, "warning"),
            ("moderate", "imminent", None, None, "warning"),
            ("moderate", "short", None, None, "caution"),
            ("far", "imminent", None, None, "caution"),
            ("far", "short", None, None, "safe"),
            ("very_close", "medium", "high_risk", "deep", "warning"),
            ("very_close", "medium", None, None, "caution"),
            ("very_close", "long", None, None, "caution"),
            ("close", "medium", "high_risk", None, "caution"),
            ("close", "medium", None, None, "caution"),
            ("close", "long", None, None, "safe"),
            ("moderate", "medium", None, None, "caution"),
            ("moderate", "long", None, None, "safe"),
            ("far", "medium", None, None, "safe"),
            ("far", "long", None, None, "safe"),
            ("moderate", "short", "low_risk", None, "caution"),
        ]

    def evaluate(self, dcpa, tcpa, ship_type="other", draught=5.0, mmsi=None):
        dcpa_mf = self._dcpa_membership(dcpa)
        tcpa_mf = self._tcpa_membership(tcpa)
        st_factor = self._ship_type_factor(ship_type)
        st_mf = self._ship_type_membership(st_factor)
        dr_mf = self._draught_membership(draught)

        output_mf = {"safe": 0.0, "caution": 0.0, "warning": 0.0, "danger": 0.0}

        for dcpa_label, tcpa_label, st_label, dr_label, risk_label in self.rules:
            strength = dcpa_mf[dcpa_label] * tcpa_mf[tcpa_label]

            if st_label is not None:
                strength *= st_mf[st_label]
            if dr_label is not None:
                strength *= dr_mf[dr_label]

            output_mf[risk_label] = max(output_mf[risk_label], strength)

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

    def _defuzzify(self, output_mf, steps=200):
        x_min, x_max = 0.0, 1.0
        step_size = (x_max - x_min) / steps
        numerator = 0.0
        denominator = 0.0

        for i in range(steps + 1):
            x = x_min + i * step_size
            risk_mf = self._risk_membership(x)
            clipped = {}
            for label, degree in output_mf.items():
                clipped[label] = min(degree, risk_mf[label])
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


_HYSTERESIS_THRESHOLDS = {
    "safe":      {"up": 0.30, "down": 0.20},
    "caution":   {"up": 0.50, "down": 0.35},
    "warning":   {"up": 0.70, "down": 0.55},
    "danger":    {"up": 0.85, "down": 0.65},
}

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
        if score >= _HYSTERESIS_THRESHOLDS["warning"]["up"]:
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


_fuzzy_instance = FuzzyCollisionRisk(ema_alpha=0.3)


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
    if risk_assessment["dcpa"] < 500 and risk_assessment["tcpa"] < 10:
        return True
    if risk_assessment["risk_score"] > 0.7:
        return True
    return False
