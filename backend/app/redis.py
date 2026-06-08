import json
import logging
from datetime import datetime

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

STREAM_AIS_RAW = "ais:raw"
STREAM_COLLISION_RISK = "collision:risk"
STREAM_ANCHOR_RISK = "anchor:risk"
STREAM_ALERT = "alert:created"

CONSUMER_GROUP_COLLISION = "collision_evaluator"
CONSUMER_GROUP_ANCHOR = "anchor_guard"
CONSUMER_GROUP_ALARM = "alarm_router"

_redis: aioredis.Redis = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=20,
        )
    return _redis


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()
        _redis = None


async def ensure_consumer_group(stream, group):
    r = await get_redis()
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass


async def xadd(stream, data: dict):
    r = await get_redis()
    serialized = {}
    for k, v in data.items():
        if v is None:
            continue
        if isinstance(v, datetime):
            v = v.isoformat()
        elif not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False, default=str)
        serialized[k] = v
    msg_id = await r.xadd(stream, serialized)
    return msg_id


async def xread_group(group, consumer, streams, count=50, block=1000):
    r = await get_redis()
    stream_names = [s[0] for s in streams]
    stream_ids = [s[1] for s in streams]
    results = await r.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={s: i for s, i in zip(stream_names, stream_ids)},
        count=count,
        block=block,
    )
    return results


async def xack(stream, group, msg_id):
    r = await get_redis()
    await r.xack(stream, group, msg_id)


def parse_stream_message(msg_data: dict) -> dict:
    parsed = {}
    for k, v in msg_data.items():
        try:
            parsed[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            parsed[k] = v
    return parsed


async def publish_batch(stream, items: list):
    if not items:
        return
    for item in items:
        await xadd(stream, item)
