import asyncio
import logging
import uuid
from datetime import datetime, timedelta

import aiohttp

from app.config import settings
from app.database import (
    get_alerts_collection,
    get_pending_pushes_collection,
)
from app.redis import (
    xread_group, xack, xadd, parse_stream_message,
    STREAM_COLLISION_RISK, STREAM_ANCHOR_RISK, STREAM_ALERT,
    CONSUMER_GROUP_ALARM, ensure_consumer_group,
)

logger = logging.getLogger(__name__)

MAX_PUSH_RETRIES = 10
INITIAL_RETRY_DELAY = 30
MAX_RETRY_DELAY = 3600


class AlarmRouter:
    def __init__(self):
        self._running = False
        self._consumer_name = "alarm_0"
        self._collision_history = {}

    async def start(self):
        self._running = True
        await ensure_consumer_group(STREAM_COLLISION_RISK, CONSUMER_GROUP_ALARM)
        await ensure_consumer_group(STREAM_ANCHOR_RISK, CONSUMER_GROUP_ALARM)
        logger.info("[AlarmRouter] started")
        asyncio.create_task(self._retry_loop())
        await self._consume_loop()

    def stop(self):
        self._running = False
        logger.info("[AlarmRouter] stopping")

    async def _consume_loop(self):
        while self._running:
            try:
                results = await xread_group(
                    CONSUMER_GROUP_ALARM,
                    self._consumer_name,
                    [
                        (STREAM_COLLISION_RISK, ">"),
                        (STREAM_ANCHOR_RISK, ">"),
                    ],
                    count=50,
                    block=1000,
                )
                if not results:
                    continue

                for stream_name, messages in results:
                    for msg_id, msg_data in messages:
                        try:
                            data = parse_stream_message(msg_data)
                            if stream_name == STREAM_COLLISION_RISK:
                                await self._handle_collision_risk(data)
                            elif stream_name == STREAM_ANCHOR_RISK:
                                await self._handle_anchor_risk(data)
                            await xack(stream_name, CONSUMER_GROUP_ALARM, msg_id)
                        except Exception as e:
                            logger.error(f"[AlarmRouter] handle error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[AlarmRouter] consume loop error: {e}")
                await asyncio.sleep(2)

    async def _handle_collision_risk(self, data):
        mmsi = data.get("mmsi")
        if not mmsi:
            return

        collision_warning = data.get("collision_warning", False)
        risk_level = data.get("risk_level", "safe")

        if not collision_warning:
            if risk_level == "danger":
                now = datetime.utcnow()
                if mmsi not in self._collision_history:
                    self._collision_history[mmsi] = {"since": None}
                history = self._collision_history[mmsi]
                if history["since"] is None:
                    history["since"] = now
                    return
                elif (now - history["since"]).total_seconds() < 60:
                    return
            else:
                if mmsi in self._collision_history:
                    self._collision_history[mmsi]["since"] = None
                return
        else:
            if mmsi in self._collision_history:
                self._collision_history[mmsi]["since"] = None

        alerts_col = get_alerts_collection()
        recent_cutoff_str = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        existing = await alerts_col.find_one({
            "mmsi": mmsi,
            "level": "level1_collision",
            "timestamp": {"$gte": recent_cutoff_str}
        })
        if existing:
            return

        alert = {
            "alert_id": uuid.uuid4().hex,
            "level": "level1_collision",
            "mmsi": mmsi,
            "ship_type": data.get("ship_type", "unknown"),
            "timestamp": datetime.utcnow().isoformat(),
            "details": {
                "risk_score": data.get("risk_score", 0),
                "dcpa": data.get("dcpa"),
                "tcpa": data.get("tcpa"),
                "nearest_turbine_id": data.get("nearest_turbine_id"),
                "in_restricted_zone": data.get("in_restricted_zone"),
                "warning_type": "collision_warning",
            },
            "push_status": {
                "maritime_center": "pending",
                "om_vessel": "pending",
            },
        }
        await alerts_col.insert_one(alert)
        await self._push_alert(alert)
        await xadd(STREAM_ALERT, alert)

    async def _handle_anchor_risk(self, data):
        mmsi = data.get("mmsi")
        if not mmsi:
            return

        anchor_damage_risk = data.get("anchor_damage_risk", False)
        if not anchor_damage_risk:
            return

        alerts_col = get_alerts_collection()
        recent_cutoff_str = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        existing = await alerts_col.find_one({
            "mmsi": mmsi,
            "level": "level2_cable",
            "timestamp": {"$gte": recent_cutoff_str}
        })
        if existing:
            return

        alert = {
            "alert_id": uuid.uuid4().hex,
            "level": "level2_cable",
            "mmsi": mmsi,
            "ship_type": data.get("ship_type", "unknown"),
            "timestamp": datetime.utcnow().isoformat(),
            "details": {
                "zone_id": data.get("zone_id"),
                "distance_to_center": data.get("distance_to_center"),
                "anchor_duration_min": data.get("duration_minutes", 3.0),
                "warning_type": "anchor_zone_violation",
            },
            "push_status": {
                "maritime_center": "pending",
                "om_vessel": "pending",
            },
        }
        await alerts_col.insert_one(alert)
        await self._push_alert(alert)
        await xadd(STREAM_ALERT, alert)

    async def _push_alert(self, alert):
        push_urls = {
            "maritime_center": settings.SATELLITE_PUSH_URL_MARITIME,
            "om_vessel": settings.SATELLITE_PUSH_URL_VESSEL,
        }
        alerts_col = get_alerts_collection()
        pending_col = get_pending_pushes_collection()

        for channel, url in push_urls.items():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, json=alert,
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        status = "sent" if resp.status == 200 else f"failed_{resp.status}"
                        await alerts_col.update_one(
                            {"alert_id": alert["alert_id"]},
                            {"$set": {f"push_status.{channel}": status}},
                        )
            except Exception as e:
                logger.warning(f"[AlarmRouter] push failed {channel}: {e}, queuing offline")
                await alerts_col.update_one(
                    {"alert_id": alert["alert_id"]},
                    {"$set": {f"push_status.{channel}": "queued"}},
                )
                await pending_col.insert_one({
                    "alert_id": alert["alert_id"],
                    "channel": channel,
                    "url": url,
                    "payload": alert,
                    "status": "queued",
                    "retry_count": 0,
                    "max_retries": MAX_PUSH_RETRIES,
                    "created_at": datetime.utcnow(),
                    "next_retry_at": datetime.utcnow() + timedelta(seconds=INITIAL_RETRY_DELAY),
                })

    async def _retry_pending_pushes(self):
        pending_col = get_pending_pushes_collection()
        alerts_col = get_alerts_collection()

        pending = await pending_col.find({
            "status": "queued",
            "retry_count": {"$lt": MAX_PUSH_RETRIES},
            "next_retry_at": {"$lte": datetime.utcnow()},
        }).to_list(length=50)

        for item in pending:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        item["url"], json=item["payload"],
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status < 300:
                            await pending_col.update_one(
                                {"_id": item["_id"]},
                                {"$set": {"status": "sent", "sent_at": datetime.utcnow()}},
                            )
                            await alerts_col.update_one(
                                {"alert_id": item["alert_id"]},
                                {"$set": {f"push_status.{item['channel']}": "sent"}},
                            )
                            logger.info(f"[AlarmRouter] offline alert {item['alert_id']} resent to {item['channel']}")
                        else:
                            raise Exception(f"HTTP {resp.status}")
            except Exception as e:
                new_retry = item["retry_count"] + 1
                if new_retry >= MAX_PUSH_RETRIES:
                    await pending_col.update_one(
                        {"_id": item["_id"]},
                        {"$set": {"status": "exhausted", "last_error": str(e)[:100]}},
                    )
                else:
                    delay = min(INITIAL_RETRY_DELAY * (2 ** new_retry), MAX_RETRY_DELAY)
                    await pending_col.update_one(
                        {"_id": item["_id"]},
                        {"$set": {
                            "retry_count": new_retry,
                            "next_retry_at": datetime.utcnow() + timedelta(seconds=delay),
                            "last_error": str(e)[:100],
                        }},
                    )

    async def _retry_loop(self):
        while self._running:
            try:
                await asyncio.sleep(15)
                await self._retry_pending_pushes()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[AlarmRouter] retry loop error: {e}")
                await asyncio.sleep(30)
