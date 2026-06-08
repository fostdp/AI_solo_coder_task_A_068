import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum

import httpx
from pymongo import ASCENDING, DESCENDING


class AlertLevel(str, Enum):
    LEVEL1_COLLISION = "level1_collision"
    LEVEL2_CABLE = "level2_cable"


class AlertManager:
    MARITIME_CENTER_URL = "https://maritime-center.example.com/api/alerts"
    OM_VESSEL_URL = "https://om-vessel.example.com/api/alerts"

    def __init__(self, db):
        self.db = db
        self._collision_history = {}

    def _build_alert_doc(self, level, mmsi, ship_type, details):
        now = datetime.now(timezone.utc)
        return {
            "alert_id": str(uuid.uuid4()),
            "level": level,
            "mmsi": mmsi,
            "ship_type": ship_type,
            "timestamp": now.isoformat(),
            "details": details,
            "push_status": {
                "maritime_center": "pending",
                "om_vessel": "pending",
            },
        }

    def evaluate_collision_alert(self, risk_assessment):
        mmsi = risk_assessment.get("mmsi")
        risk_level = risk_assessment.get("risk_level", "safe")
        now = datetime.now(timezone.utc)

        if mmsi not in self._collision_history:
            self._collision_history[mmsi] = {"since": None}

        history = self._collision_history[mmsi]

        if risk_level == "danger":
            if history["since"] is None:
                history["since"] = now
            elif (now - history["since"]).total_seconds() >= 60:
                existing = self.db["alerts"].find_one(
                    {"mmsi": mmsi, "level": AlertLevel.LEVEL1_COLLISION, "timestamp": {"$gte": history["since"].isoformat()}}
                )
                if existing:
                    return None

                details = {
                    "risk_score": risk_assessment.get("risk_score", 0.0),
                    "dcpa": risk_assessment.get("dcpa"),
                    "tcpa": risk_assessment.get("tcpa"),
                }
                alert = self._build_alert_doc(
                    AlertLevel.LEVEL1_COLLISION,
                    mmsi,
                    risk_assessment.get("ship_type", "unknown"),
                    details,
                )
                self.db["alerts"].insert_one(alert)
                alert.pop("_id", None)
                return alert
        else:
            history["since"] = None

        return None

    def evaluate_cable_alert(self, anchor_assessment):
        if not anchor_assessment.get("anchor_damage_risk"):
            return None

        mmsi = anchor_assessment.get("mmsi")
        recent_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        existing = self.db["alerts"].find_one(
            {"mmsi": mmsi, "level": AlertLevel.LEVEL2_CABLE, "timestamp": {"$gte": recent_cutoff}}
        )
        if existing:
            return None

        details = {
            "risk_score": anchor_assessment.get("risk_score", 0.0),
            "zone_id": anchor_assessment.get("zone_id"),
            "anchor_duration_min": anchor_assessment.get("duration_minutes"),
        }
        alert = self._build_alert_doc(
            AlertLevel.LEVEL2_CABLE,
            mmsi,
            anchor_assessment.get("ship_type", "unknown"),
            details,
        )
        self.db["alerts"].insert_one(alert)
        alert.pop("_id", None)
        return alert

    def push_alert(self, alert):
        alert_id = alert["alert_id"]
        results = {"maritime_center": "failed", "om_vessel": "failed"}

        for target, url_key in [("maritime_center", self.MARITIME_CENTER_URL), ("om_vessel", self.OM_VESSEL_URL)]:
            try:
                print(f"[ALERT PUSH] Sending alert {alert_id} to {target} at {url_key}")
                resp = httpx.post(url_key, json=alert, timeout=5.0)
                if resp.status_code < 300:
                    results[target] = "sent"
                    print(f"[ALERT PUSH] Alert {alert_id} delivered to {target} (HTTP {resp.status_code})")
                else:
                    print(f"[ALERT PUSH] Alert {alert_id} rejected by {target} (HTTP {resp.status_code})")
            except httpx.HTTPError as exc:
                print(f"[ALERT PUSH] Alert {alert_id} failed for {target}: {exc}")

        self.db["alerts"].update_one(
            {"alert_id": alert_id},
            {"$set": {"push_status": results}},
        )
        return results

    def get_alert_history(self, hours=24):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        alerts = list(self.db["alerts"].find({"timestamp": {"$gte": cutoff}}).sort("timestamp", DESCENDING))
        for a in alerts:
            a.pop("_id", None)
        return alerts

    def get_monthly_stats(self):
        now = datetime.now(timezone.utc)
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()

        total = self.db["alerts"].count_documents({"timestamp": {"$gte": month_start}})
        level1_count = self.db["alerts"].count_documents(
            {"level": AlertLevel.LEVEL1_COLLISION, "timestamp": {"$gte": month_start}}
        )
        level2_count = self.db["alerts"].count_documents(
            {"level": AlertLevel.LEVEL2_CABLE, "timestamp": {"$gte": month_start}}
        )

        by_day = {}
        day_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        while day_start <= now:
            day_key = day_start.strftime("%Y-%m-%d")
            day_end = day_start + timedelta(days=1)
            count = self.db["alerts"].count_documents(
                {"timestamp": {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}}
            )
            by_day[day_key] = count
            day_start = day_end

        by_ship_type = {}
        pipeline = [
            {"$match": {"timestamp": {"$gte": month_start}}},
            {"$group": {"_id": "$ship_type", "count": {"$sum": 1}}},
        ]
        for doc in self.db["alerts"].aggregate(pipeline):
            by_ship_type[doc["_id"] or "unknown"] = doc["count"]

        return {
            "total_alerts": total,
            "level1_count": level1_count,
            "level2_count": level2_count,
            "by_day": by_day,
            "by_ship_type": by_ship_type,
        }
