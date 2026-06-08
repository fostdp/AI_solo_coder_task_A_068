import json
import asyncio
import logging
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt

from app.config import settings
from app.database import (
    get_ships_collection,
    get_traffic_logs_collection,
    get_ship_tracks_collection,
    get_alerts_collection,
    get_risk_assessments_collection,
    get_turbines_collection,
    get_restricted_zones_collection,
)
from app.services.collision import assess_collision_risk, check_collision_warning

logger = logging.getLogger(__name__)


class MQTTHandler:
    def __init__(self):
        self.client = None
        self._loop = None
        self._running = False

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT connected successfully")
            client.subscribe("windfarm/turbine/+/ais")
            client.subscribe("windfarm/ships/all")
            logger.info("Subscribed to MQTT topics")
        else:
            logger.error(f"MQTT connection failed with code {rc}")

    def _on_message(self, client, userdata, msg):
        if self._loop is None:
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            topic = msg.topic
            asyncio.run_coroutine_threadsafe(
                self._handle_message(topic, payload), self._loop
            )
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    async def _handle_message(self, topic, payload):
        try:
            if topic.startswith("windfarm/turbine/"):
                parts = topic.split("/")
                turbine_id = parts[2] if len(parts) > 2 else "unknown"
                await self._process_turbine_ais(turbine_id, payload)
            elif topic == "windfarm/ships/all":
                await self._process_all_ships(payload)
        except Exception as e:
            logger.error(f"Error in _handle_message: {e}")

    async def _process_turbine_ais(self, turbine_id, payload):
        ships = payload.get("ships", [])
        ships_col = get_ships_collection()
        tracks_col = get_ship_tracks_collection()
        logs_col = get_traffic_logs_collection()

        for ship_data in ships:
            mmsi = ship_data.get("mmsi")
            if not mmsi:
                continue
            ship_data["updated_at"] = datetime.utcnow()
            await ships_col.update_one(
                {"mmsi": mmsi},
                {"$set": ship_data},
                upsert=True,
            )
            track_entry = {
                "mmsi": mmsi,
                "lat": ship_data.get("lat"),
                "lng": ship_data.get("lng"),
                "speed": ship_data.get("speed", 0),
                "course": ship_data.get("course", 0),
                "nav_status": ship_data.get("nav_status", ""),
                "timestamp": datetime.utcnow(),
                "source": turbine_id,
            }
            await tracks_col.insert_one(track_entry)

        await logs_col.insert_one(
            {
                "timestamp": datetime.utcnow(),
                "ship_count": len(ships),
                "ships": ships,
                "source": turbine_id,
            }
        )

        for ship_data in ships:
            mmsi = ship_data.get("mmsi")
            if mmsi:
                await self._evaluate_risk(mmsi, ship_data)

    async def _process_all_ships(self, payload):
        ships = payload.get("ships", [])
        ships_col = get_ships_collection()
        tracks_col = get_ship_tracks_collection()
        logs_col = get_traffic_logs_collection()

        for ship_data in ships:
            mmsi = ship_data.get("mmsi")
            if not mmsi:
                continue
            ship_data["updated_at"] = datetime.utcnow()
            await ships_col.update_one(
                {"mmsi": mmsi},
                {"$set": ship_data},
                upsert=True,
            )
            track_entry = {
                "mmsi": mmsi,
                "lat": ship_data.get("lat"),
                "lng": ship_data.get("lng"),
                "speed": ship_data.get("speed", 0),
                "course": ship_data.get("course", 0),
                "nav_status": ship_data.get("nav_status", ""),
                "timestamp": datetime.utcnow(),
                "source": "bulk_update",
            }
            await tracks_col.insert_one(track_entry)

        await logs_col.insert_one(
            {
                "timestamp": datetime.utcnow(),
                "ship_count": len(ships),
                "ships": ships,
                "source": "bulk_update",
            }
        )

        for ship_data in ships:
            mmsi = ship_data.get("mmsi")
            if mmsi:
                await self._evaluate_risk(mmsi, ship_data)

    async def _evaluate_risk(self, mmsi, ship_data):
        risk_col = get_risk_assessments_collection()
        turbines_col = get_turbines_collection()
        zones_col = get_restricted_zones_collection()
        alerts_col = get_alerts_collection()

        turbines = await turbines_col.find({}).to_list(length=None)
        zones = await zones_col.find({}).to_list(length=None)

        risk_assessment = assess_collision_risk(ship_data, turbines, zones)

        ship_lat = ship_data.get("lat", 0)
        ship_lng = ship_data.get("lng", 0)

        nearest_turbine = None
        for t in turbines:
            if t.get("turbine_id") == risk_assessment.get("nearest_turbine_id"):
                nearest_turbine = t
                break

        risk_data = {
            "mmsi": mmsi,
            "risk_score": risk_assessment["risk_score"],
            "risk_level": risk_assessment["risk_level"],
            "dcpa": risk_assessment["dcpa"],
            "tcpa": risk_assessment["tcpa"],
            "nearest_turbine_id": risk_assessment["nearest_turbine_id"],
            "in_restricted_zone": risk_assessment["in_restricted_zone"],
            "estimated_entry_time": risk_assessment["estimated_entry_time"],
            "ship_lat": ship_lat,
            "ship_lng": ship_lng,
            "target_lat": nearest_turbine["lat"] if nearest_turbine else None,
            "target_lng": nearest_turbine["lng"] if nearest_turbine else None,
            "updated_at": datetime.utcnow(),
        }
        await risk_col.update_one(
            {"mmsi": mmsi},
            {"$set": risk_data},
            upsert=True,
        )

        if check_collision_warning(risk_assessment):
            await self._create_alert(mmsi, ship_data, risk_assessment, "level1_collision")

        nav_status = ship_data.get("nav_status", "")
        speed = ship_data.get("speed", 0)
        if nav_status == "at_anchor" or speed < 1.0:
            from app.services.anchor_warning import check_anchor_in_restricted_zone
            zone_check = check_anchor_in_restricted_zone(ship_data, zones)
            if zone_check["in_zone"]:
                await self._create_anchor_alert(mmsi, ship_data, zone_check)

    async def _create_alert(self, mmsi, ship_data, risk_assessment, level):
        import uuid

        alerts_col = get_alerts_collection()

        recent_cutoff_str = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        existing = await alerts_col.find_one({
            "mmsi": mmsi,
            "level": level,
            "timestamp": {"$gte": recent_cutoff_str}
        })
        if existing:
            return

        alert_details = {
            "risk_score": risk_assessment.get("risk_score", 0),
            "dcpa": risk_assessment.get("dcpa"),
            "tcpa": risk_assessment.get("tcpa"),
            "nearest_turbine_id": risk_assessment.get("nearest_turbine_id"),
            "in_restricted_zone": risk_assessment.get("in_restricted_zone"),
            "warning_type": "collision_warning",
        }

        alert = {
            "alert_id": uuid.uuid4().hex,
            "level": level,
            "mmsi": mmsi,
            "ship_type": ship_data.get("ship_type", "unknown"),
            "timestamp": datetime.utcnow().isoformat(),
            "details": alert_details,
            "push_status": {
                "maritime_center": "pending",
                "om_vessel": "pending",
            },
        }
        await alerts_col.insert_one(alert)
        await self._push_alert(alert)

    async def _create_anchor_alert(self, mmsi, ship_data, zone_check):
        import uuid

        alerts_col = get_alerts_collection()

        recent_cutoff_str = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        existing = await alerts_col.find_one({
            "mmsi": mmsi,
            "level": "level2_cable",
            "timestamp": {"$gte": recent_cutoff_str}
        })
        if existing:
            return

        alert_details = {
            "zone_id": zone_check.get("zone_id"),
            "distance_to_center": zone_check.get("distance_to_center"),
            "anchor_duration_min": 3.0,
            "warning_type": "anchor_zone_violation",
        }

        alert = {
            "alert_id": uuid.uuid4().hex,
            "level": "level2_cable",
            "mmsi": mmsi,
            "ship_type": ship_data.get("ship_type", "unknown"),
            "timestamp": datetime.utcnow().isoformat(),
            "details": alert_details,
            "push_status": {
                "maritime_center": "pending",
                "om_vessel": "pending",
            },
        }
        await alerts_col.insert_one(alert)
        await self._push_alert(alert)

    async def _push_alert(self, alert):
        import aiohttp

        push_urls = {
            "maritime_center": settings.SATELLITE_PUSH_URL_MARITIME,
            "om_vessel": settings.SATELLITE_PUSH_URL_VESSEL,
        }
        alerts_col = get_alerts_collection()
        for channel, url in push_urls.items():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=alert, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        status = "sent" if resp.status == 200 else f"failed_{resp.status}"
                        await alerts_col.update_one(
                            {"alert_id": alert["alert_id"]},
                            {"$set": {f"push_status.{channel}": status}},
                        )
            except Exception as e:
                logger.error(f"Alert push failed for {channel}: {e}")
                await alerts_col.update_one(
                    {"alert_id": alert["alert_id"]},
                    {"$set": {f"push_status.{channel}": f"error:{str(e)[:50]}"}},
                )

    def start(self, loop):
        self._loop = loop
        self._running = True
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        try:
            self.client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
            self.client.loop_start()
            logger.info("MQTT handler started")
        except Exception as e:
            logger.error(f"MQTT connection error: {e}")
            self._running = False

    def stop(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("MQTT handler stopped")
        self._running = False
