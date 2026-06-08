import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import connect_to_mongo, close_mongo_connection, get_database
from app.routes.api import router as api_router
from app.services.mqtt_handler import MQTTHandler

app = FastAPI(title="海上风电场海缆保护与船舶碰撞预警系统")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

mqtt_handler = MQTTHandler()

ws_clients = []


@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()
    loop = asyncio.get_event_loop()
    mqtt_handler.start(loop)
    asyncio.create_task(broadcast_loop())


@app.on_event("shutdown")
async def shutdown_event():
    mqtt_handler.stop()
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
