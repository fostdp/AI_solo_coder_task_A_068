from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

client: AsyncIOMotorClient = None
database = None


async def connect_to_mongo():
    global client, database
    client = AsyncIOMotorClient(settings.MONGO_URI)
    database = client[settings.MONGO_DB]


async def close_mongo_connection():
    global client
    if client:
        client.close()


def get_database():
    return database


def get_ships_collection():
    return database["ships"]


def get_turbines_collection():
    return database["turbines"]


def get_cables_collection():
    return database["cables"]


def get_restricted_zones_collection():
    return database["restricted_zones"]


def get_alerts_collection():
    return database["alerts"]


def get_risk_assessments_collection():
    return database["risk_assessments"]


def get_traffic_logs_collection():
    return database["traffic_logs"]


def get_ship_tracks_collection():
    return database["ship_tracks"]


def get_pending_pushes_collection():
    return database["pending_pushes"]
