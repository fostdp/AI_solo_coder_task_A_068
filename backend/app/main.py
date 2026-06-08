import asyncio
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import connect_to_mongo, close_mongo_connection, get_database
from app.redis import get_redis, close_redis
from app.routes.api import router as api_router
from app.services.ais_ingestor import AISIngestor
from app.services.collision_evaluator import CollisionEvaluator
from app.services.anchor_guard import AnchorGuard
from app.services.alarm_router import AlarmRouter

app = FastAPI(title="海上风电场海缆保护与船舶碰撞预警系统")

app.add_middleware(GZipMiddleware, minimum_size=500)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

ais_ingestor = AISIngestor()
collision_evaluator = CollisionEvaluator()
anchor_guard = AnchorGuard()
alarm_router = AlarmRouter()

ws_clients = []

_evaluator_task = None
_anchor_task = None
_alarm_task = None


@app.on_event("startup")
async def startup_event():
    global _evaluator_task, _anchor_task, _alarm_task
    await connect_to_mongo()
    await get_redis()

    loop = asyncio.get_event_loop()
    await ais_ingestor.start(loop)

    _evaluator_task = asyncio.create_task(collision_evaluator.start())
    _anchor_task = asyncio.create_task(anchor_guard.start())
    _alarm_task = asyncio.create_task(alarm_router.start())

    asyncio.create_task(broadcast_loop())


@app.on_event("shutdown")
async def shutdown_event():
    collision_evaluator.stop()
    anchor_guard.stop()
    alarm_router.stop()
    ais_ingestor.stop()
    await close_redis()
    await close_mongo_connection()


frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
frontend_static = frontend_dir / "static"

if frontend_static.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_static)), name="static")


@app.get("/")
async def serve_index():
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "海上风电场海缆保护与船舶碰撞预警系统 API", "docs": "/docs"}


@app.websocket("/ws")
async def websocket_realtime(websocket: WebSocket):
    await websocket.accept()
    ws_clients.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        if websocket in ws_clients:
            ws_clients.remove(websocket)


async def broadcast_loop():
    while True:
        try:
            await asyncio.sleep(3)
            db = get_database()
            if db is None:
                continue
            ships = await db["ships"].find({}).to_list(length=None)
            for s in ships:
                s["_id"] = str(s["_id"])
            risks = await db["risk_assessments"].find({}).to_list(length=None)
            for r in risks:
                r["_id"] = str(r["_id"])
            message = {
                "type": "ship_update",
                "ships": ships,
                "risk_assessments": risks,
                "timestamp": datetime.utcnow().isoformat(),
            }
            disconnected = []
            for client in ws_clients:
                try:
                    await client.send_json(message)
                except Exception:
                    disconnected.append(client)
            for d in disconnected:
                if d in ws_clients:
                    ws_clients.remove(d)
        except Exception:
            await asyncio.sleep(5)
