import asyncio
import logging
from datetime import datetime
from collections import defaultdict

from app.database import (
    get_turbines_collection,
    get_restricted_zones_collection,
    get_risk_assessments_collection,
)
from app.redis import (
    xread_group, xack, xadd, parse_stream_message,
    STREAM_AIS_RAW, STREAM_COLLISION_RISK,
    CONSUMER_GROUP_COLLISION, ensure_consumer_group,
)
from app.services.collision import (
    assess_collision_risk,
    check_collision_warning,
    SpatialGridIndex,
)

logger = logging.getLogger(__name__)


class CollisionEvaluator:
    def __init__(self):
        self._running = False
        self._turbines_cache = None
        self._zones_cache = None
        self._turbine_grid = None
        self._cache_ts = None
        self._consumer_name = "eval_0"

    async def _ensure_cache(self):
        now = datetime.utcnow()
        if self._cache_ts and (now - self._cache_ts).total_seconds() < 30:
            return
        turbines_col = get_turbines_collection()
        zones_col = get_restricted_zones_collection()
        self._turbines_cache = await turbines_col.find({}).to_list(length=None)
        self._zones_cache = await zones_col.find({}).to_list(length=None)
        self._turbine_grid = SpatialGridIndex()
        for t in self._turbines_cache:
            self._turbine_grid.insert(t)
        self._cache_ts = now

    async def start(self):
        self._running = True
        await ensure_consumer_group(STREAM_AIS_RAW, CONSUMER_GROUP_COLLISION)
        logger.info("[CollisionEvaluator] started")
        await self._consume_loop()

    def stop(self):
        self._running = False
        logger.info("[CollisionEvaluator] stopping")

    async def _consume_loop(self):
        while self._running:
            try:
                results = await xread_group(
                    CONSUMER_GROUP_COLLISION,
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
                            await self._evaluate_ship(ship)
                            await xack(STREAM_AIS_RAW, CONSUMER_GROUP_COLLISION, msg_id)
                        except Exception as e:
                            logger.error(f"[CollisionEvaluator] evaluate error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[CollisionEvaluator] consume loop error: {e}")
                await asyncio.sleep(2)

    async def _evaluate_ship(self, ship):
        await self._ensure_cache()
        turbines = self._turbines_cache
        zones = self._zones_cache
        turbine_grid = self._turbine_grid

        if not turbines:
            return

        mmsi = ship.get("mmsi")
        if not mmsi:
            return

        ship_lat = ship.get("lat", 0)
        ship_lng = ship.get("lng", 0)
        nearby_turbines = turbine_grid.query_nearby(ship_lat, ship_lng, radius_cells=2)

        risk_assessment = assess_collision_risk(ship, nearby_turbines, zones)

        nearest_turbine = None
        for t in nearby_turbines:
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
        }

        risk_col = get_risk_assessments_collection()
        now = datetime.utcnow()
        risk_data["updated_at"] = now
        await risk_col.update_one({"mmsi": mmsi}, {"$set": risk_data}, upsert=True)

        risk_data["collision_warning"] = check_collision_warning(risk_assessment)
        risk_data["ship_type"] = ship.get("ship_type", "other")
        risk_data["timestamp"] = now.isoformat()

        await xadd(STREAM_COLLISION_RISK, risk_data)
