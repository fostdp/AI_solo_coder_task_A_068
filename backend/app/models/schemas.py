from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class ShipData(BaseModel):
    mmsi: int
    lat: float
    lng: float
    speed: float = 0.0
    course: float = 0.0
    draught: Optional[float] = None
    ship_type: Optional[str] = None
    nav_status: Optional[str] = None
    scour_depth: Optional[float] = None


class TurbineData(BaseModel):
    turbine_id: str
    lat: float
    lng: float
    status: str = "active"
    scour_depth: Optional[float] = None
    is_substation: bool = False


class CableRoute(BaseModel):
    route_id: str
    points: List[List[float]]
    type: str = "inter_array"
    status: str = "active"


class RestrictedZone(BaseModel):
    zone_id: str
    center: List[float]
    radius_meters: float
    type: str = "anchor"


class RiskAssessment(BaseModel):
    mmsi: int
    risk_score: float = 0.0
    risk_level: str = "low"
    dcpa: Optional[float] = None
    tcpa: Optional[float] = None
    nearest_turbine_id: Optional[str] = None
    in_restricted_zone: bool = False
    estimated_entry_time: Optional[datetime] = None


class AlertData(BaseModel):
    alert_id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex)
    level: str
    mmsi: Optional[int] = None
    ship_type: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    details: Dict[str, Any] = {}
    push_status: Dict[str, Any] = {}


class TrafficLog(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    ship_count: int = 0
    ships: List[ShipData] = []


class AISMessage(BaseModel):
    turbine_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    sensor_type: str = "ais"
    ships: List[ShipData] = []
