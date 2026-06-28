from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_cloud_project: str
    vertex_ai_location: str = "global"
    firebase_project_id: str
    frontend_origin: str = "http://localhost:3000"

    # Agent Engine（マネージドサービス利用: コード実行サンドボックス + セッションストア）
    # provision スクリプトで作成した ReasoningEngine の resource name / id を設定する。
    # Gemini 呼び出しは vertex_ai_location=global だが、Agent Engine はリージョン必須なので分離する。
    agent_engine_resource_name: str = ""
    agent_engine_id: str = ""
    agent_runtime_location: str = "us-central1"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
