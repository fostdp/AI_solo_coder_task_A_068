import json
import asyncio
import logging
from datetime import datetime
from collections import defaultdict

import paho.mqtt.client as mqtt

from app.config import settings
from app.database import (
    get_ships_collection,
    get_traffic_logs_collection,
    get_ship_tracks_collection,
)
from app.redis import xadd, STREAM_AIS_RAW, ensure_consumer_group

logger = logging.getLogger(__name__)


class AISIngestor:
    def __init__(self):
        self.client = None
        self._loop = None
        self._running = False

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("[AISIngestor] MQTT connected")
            client.subscribe("windfarm/turbine/+/ais")
            client.subscribe("windfarm/ships/all")
        else:
            logger.error(f"[AISIngestor] MQTT connect failed: {rc}")

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
            logger.error(f"[AISIngestor] MQTT message error: {e}")

    async def _handle_message(self, topic, payload):
        try:
            source = "bulk_update"
            if topic.startswith("windfarm/turbine/"):
                parts = topic.split("/")
                source = parts[2] if len(parts) > 2 else "unknown"

            ships = payload.get("ships", [])
            if not ships:
                return

            await self._persist_ships(ships, source)
            await self._publish_to_stream(ships, source)
        except Exception as e:
            logger.error(f"[AISIngestor] handle_message error: {e}")

    async def _persist_ships(self, ships_data, source):
        if not ships_data:
            return
        ships_col = get_ships_collection()
        tracks_col = get_ship_tracks_collection()
        logs_col = get_traffic_logs_collection()
        now = datetime.utcnow()

        ship_writes = []
        track_writes = []
        for ship_data in ships_data:
            mmsi = ship_data.get("mmsi")
            if not mmsi:
                continue
            ship_data["updated_at"] = now
            ship_writes.append(
                ships_col.update_one({"mmsi": mmsi}, {"$set": ship_data}, upsert=True)
            )
            track_writes.append(
                tracks_col.insert_one({
                    "mmsi": mmsi,
                    "lat": ship_data.get("lat"),
                    "lng": ship_data.get("lng"),
                    "speed": ship_data.get("speed", 0),
                    "course": ship_data.get("course", 0),
                    "nav_status": ship_data.get("nav_status", ""),
                    "timestamp": now,
                    "source": source,
                })
            )

        await asyncio.gather(*ship_writes, return_exceptions=True)
        if track_writes:
            await asyncio.gather(*track_writes, return_exceptions=True)

        await logs_col.insert_one({
            "timestamp": now,
            "ship_count": len(ships_data),
            "ships": ships_data,
            "source": source,
        })

    async def _publish_to_stream(self, ships_data, source):
        now = datetime.utcnow().isoformat()
        for ship_data in ships_data:
            mmsi = ship_data.get("mmsi")
            if not mmsi:
                continue
            msg = {
                "mmsi": mmsi,
                "lat": ship_data.get("lat"),
                "lng": ship_data.get("lng"),
                "speed": ship_data.get("speed", 0),
                "course": ship_data.get("course", 0),
                "draught": ship_data.get("draught", 5.0),
                "ship_type": ship_data.get("ship_type", "other"),
                "nav_status": ship_data.get("nav_status", ""),
                "scour_depth": ship_data.get("scour_depth"),
                "source": source,
                "timestamp": now,
            }
            await xadd(STREAM_AIS_RAW, msg)

    async def start(self, loop):
        self._loop = loop
        self._running = True
        await ensure_consumer_group(STREAM_AIS_RAW, "collision_evaluator")
        await ensure_consumer_group(STREAM_AIS_RAW, "anchor_guard")

        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        try:
            self.client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
            self.client.loop_start()
            logger.info("[AISIngestor] started")
        except Exception as e:
            logger.error(f"[AISIngestor] MQTT connect error: {e}")
            self._running = False

    def stop(self):
        self._running = False
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("[AISIngestor] stopped")
