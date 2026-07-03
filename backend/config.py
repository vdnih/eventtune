from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_cloud_project: str
    vertex_ai_location: str = "us"
    firebase_project_id: str
    frontend_origin: str = "http://localhost:3000"

    # Agent Engine（マネージドサービス利用: コード実行サンドボックス + セッションストア）
    # provision スクリプトで作成した ReasoningEngine の resource name / id を設定する。
    agent_engine_resource_name: str = ""
    agent_engine_id: str = ""
    agent_runtime_location: str = "us-central1"

    # Gemini モデル設定（処理特性ごとに分離）
    model_ingestion: str = "gemini-3.5-flash"  # スキーマ理解・ドキュメント解析・要約
    model_batch: str = "gemini-3.1-flash-lite"  # 行単位軽量抽出（高ボリューム・並列）
    model_agent: str = "gemini-3.5-flash"  # エージェント推論・分類判定
    model_content: str = "gemini-3.5-flash"  # コンテンツ・メール生成

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
