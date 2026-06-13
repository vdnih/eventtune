from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_cloud_project: str
    vertex_ai_location: str = "global"
    firebase_project_id: str
    frontend_origin: str = "http://localhost:3000"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
