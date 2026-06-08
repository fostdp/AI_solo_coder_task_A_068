import asyncio
import logging
from datetime import datetime

from app.database import (
    get_restricted_zones_collection,
    get_ship_tracks_collection,
)
from app.redis import (
    xread_group, xack, xadd, parse_stream_message,
    STREAM_AIS_RAW, STREAM_ANCHOR_RISK,
    CONSUMER_GROUP_ANCHOR, ensure_consumer_group,
)
from app.services.anchor_warning import check_anchor_in_restricted_zone, assess_anchor_risk

logger = logging.getLogger(__name__)


class AnchorGuard:
    def __init__(self):
        self._running = False
        self._zones_cache = None
        self._cache_ts = None
        self._consumer_name = "guard_0"

    async def _ensure_cache(self):
        now = datetime.utcnow()
        if self._cache_ts and (now - self._cache_ts).total_seconds() < 30:
            return
        zones_col = get_restricted_zones_collection()
        self._zones_cache = await zones_col.find({}).to_list(length=None)
        self._cache_ts = now

    async def start(self):
        self._running = True
        await ensure_consumer_group(STREAM_AIS_RAW, CONSUMER_GROUP_ANCHOR)
        logger.info("[AnchorGuard] started")
        await self._consume_loop()

    def stop(self):
        self._running = False
        logger.info("[AnchorGuard] stopping")

    async def _consume_loop(self):
        while self._running:
            try:
                results = await xread_group(
                    CONSUMER_GROUP_ANCHOR,
                    self._consumer_name,
                    [(STREAM_AIS_RAW, ">")],
                    count=50,
                    block=1000,
                )
                if not results:
                    continue

                for stream_name, messages in results:
                    for msg_id, msg_data in messages:
                        try:
                            ship = parse_stream_message(msg_data)
                            await self._evaluate_anchor(ship)
                            await xack(STREAM_AIS_RAW, CONSUMER_GROUP_ANCHOR, msg_id)
                        except Exception as e:
                            logger.error(f"[AnchorGuard] evaluate error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[AnchorGuard] consume loop error: {e}")
                await asyncio.sleep(2)

    async def _evaluate_anchor(self, ship):
        await self._ensure_cache()
        zones = self._zones_cache
        if not zones:
            return

        nav_status = ship.get("nav_status", "")
        speed = ship.get("speed", 0)
        is_anchored = nav_status == "at_anchor" or speed < 1.0
        if not is_anchored:
            return

        mmsi = ship.get("mmsi")
        if not mmsi:
            return

        zone_check = check_anchor_in_restricted_zone(ship, zones)
        if not zone_check["in_zone"]:
            return

        from app.database import get_database
        db = get_database()
        if db is None:
            return

        anchor_assessment = assess_anchor_risk(ship, zones, db)
        anchor_assessment["mmsi"] = mmsi
        anchor_assessment["ship_type"] = ship.get("ship_type", "other")
        anchor_assessment["timestamp"] = datetime.utcnow().isoformat()

        await xadd(STREAM_ANCHOR_RISK, anchor_assessment)
