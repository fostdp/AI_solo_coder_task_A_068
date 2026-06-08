from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB: str = "windfarm_warning"
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = 1883
    SATELLITE_PUSH_URL_MARITIME: str = "http://localhost:8001/api/maritime/alert"
    SATELLITE_PUSH_URL_VESSEL: str = "http://localhost:8001/api/vessel/alert"
    CORS_ORIGINS: List[str] = ["*"]
    WINDFARM_CENTER_LAT: float = 31.0
    WINDFARM_CENTER_LNG: float = 121.5

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
