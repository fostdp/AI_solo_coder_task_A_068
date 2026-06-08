from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from datetime import datetime, timedelta
from typing import List, Optional
import json
import asyncio

from app.database import (
    get_ships_collection,
    get_turbines_collection,
    get_cables_collection,
    get_restricted_zones_collection,
    get_alerts_collection,
    get_risk_assessments_collection,
    get_traffic_logs_collection,
    get_ship_tracks_collection,
)
from app.models.schemas import AISMessage, ShipData
from app.redis import xadd, STREAM_AIS_RAW

router = APIRouter(prefix="/api")

websocket_clients: List[WebSocket] = []


@router.get("/turbines")
async def list_turbines():
    collection = get_turbines_collection()
    turbines = await collection.find({}).to_list(length=None)
    for t in turbines:
        t["_id"] = str(t["_id"])
    return turbines


@router.get("/cables")
async def list_cables():
    collection = get_cables_collection()
    cables = await collection.find({}).to_list(length=None)
    for c in cables:
        c["_id"] = str(c["_id"])
    return cables


@router.get("/restricted-zones")
async def list_restricted_zones():
    collection = get_restricted_zones_collection()
    zones = await collection.find({}).to_list(length=None)
    for z in zones:
        z["_id"] = str(z["_id"])
    return zones


@router.get("/ships")
async def list_ships():
    collection = get_ships_collection()
    ships = await collection.find({}).to_list(length=None)
    for s in ships:
        s["_id"] = str(s["_id"])
    return ships


@router.get("/ships/{mmsi}")
async def get_ship_detail(mmsi: int):
    ships_col = get_ships_collection()
    tracks_col = get_ship_tracks_collection()
    ship = await ships_col.find_one({"mmsi": mmsi})
    if not ship:
        return {"error": "Ship not found"}
    ship["_id"] = str(ship["_id"])
    tracks = await tracks_col.find({"mmsi": mmsi}).sort("timestamp", -1).to_list(length=100)
    for t in tracks:
        t["_id"] = str(t["_id"])
    return {"ship": ship, "track_history": tracks}


@router.get("/risk-assessment")
async def get_risk_assessments():
    collection = get_risk_assessments_collection()
    assessments = await collection.find({}).to_list(length=None)
    for a in assessments:
        a["_id"] = str(a["_id"])
    return assessments


@router.get("/alerts")
async def get_alerts(
    hours: int = Query(default=24, ge=1, le=720),
    level: Optional[str] = Query(default=None),
):
    collection = get_alerts_collection()
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    query = {"timestamp": {"$gte": cutoff}}
    if level:
        query["level"] = level
    alerts = await collection.find(query).sort("timestamp", -1).to_list(length=None)
    for a in alerts:
        a["_id"] = str(a["_id"])
        if "timestamp" in a and isinstance(a["timestamp"], datetime):
            a["timestamp"] = a["timestamp"].isoformat()
    return alerts


@router.get("/alerts/stats")
async def get_alert_stats():
    collection = get_alerts_collection()
    pipeline = [
        {
            "$group": {
                "_id": {
                    "year": {"$year": "$timestamp"},
                    "month": {"$month": "$timestamp"},
                    "level": "$level",
                },
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"_id.year": 1, "_id.month": 1}},
    ]
    results = await collection.aggregate(pipeline).to_list(length=None)
    stats = []
    for r in results:
        stats.append(
            {
                "year": r["_id"]["year"],
                "month": r["_id"]["month"],
                "level": r["_id"]["level"],
                "count": r["count"],
            }
        )
    return stats


@router.get("/traffic/heatmap")
async def get_traffic_heatmap():
    collection = get_traffic_logs_collection()
    cutoff = datetime.utcnow() - timedelta(hours=24)
    logs = await collection.find({"timestamp": {"$gte": cutoff}}).to_list(length=None)
    positions = []
    for log in logs:
        ts = log.get("timestamp")
        if isinstance(ts, datetime):
            ts = ts.isoformat()
        for ship in log.get("ships", []):
            positions.append(
                {
                    "lat": ship.get("lat"),
                    "lng": ship.get("lng"),
                    "timestamp": ts,
                    "mmsi": ship.get("mmsi"),
                }
            )
    return {"positions": positions, "count": len(positions)}


@router.get("/traffic/stats")
async def get_traffic_stats():
    logs_col = get_traffic_logs_collection()
    ships_col = get_ships_collection()
    total_logs = await logs_col.count_documents({})
    current_ships = await ships_col.count_documents({})
    pipeline = [
        {
            "$group": {
                "_id": None,
                "avg_ship_count": {"$avg": "$ship_count"},
                "max_ship_count": {"$max": "$ship_count"},
            }
        }
    ]
    result = await logs_col.aggregate(pipeline).to_list(length=1)
    avg_count = result[0]["avg_ship_count"] if result else 0
    max_count = result[0]["max_ship_count"] if result else 0
    return {
        "current_ship_count": current_ships,
        "average_ship_count": round(avg_count, 1),
        "max_ship_count": max_count,
        "total_traffic_logs": total_logs,
    }


@router.post("/ais-data")
async def receive_ais_data(message: AISMessage):
    now_iso = datetime.utcnow().isoformat()
    for ship in message.ships:
        ship_dict = ship.model_dump()
        msg = {
            "mmsi": ship.mmsi,
            "lat": ship.lat,
            "lng": ship.lng,
            "speed": ship.speed,
            "course": ship.course,
            "draught": ship.draught,
            "ship_type": ship.ship_type,
            "nav_status": ship.nav_status,
            "scour_depth": ship.scour_depth,
            "source": message.turbine_id,
            "timestamp": now_iso,
        }
        await xadd(STREAM_AIS_RAW, msg)
    return {"status": "ok", "ships_processed": len(message.ships)}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_clients.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        websocket_clients.remove(websocket)


async def broadcast_ship_update():
    ships_col = get_ships_collection()
    risk_col = get_risk_assessments_collection()
    ships = await ships_col.find({}).to_list(length=None)
    for s in ships:
        s["_id"] = str(s["_id"])
    risks = await risk_col.find({}).to_list(length=None)
    for r in risks:
        r["_id"] = str(r["_id"])
    message = {
        "type": "ship_update",
        "ships": ships,
        "risk_assessments": risks,
        "timestamp": datetime.utcnow().isoformat(),
    }
    disconnected = []
    for client in websocket_clients:
        try:
            await client.send_json(message)
        except Exception:
            disconnected.append(client)
    for d in disconnected:
        if d in websocket_clients:
            websocket_clients.remove(d)
