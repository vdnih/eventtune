"""
Agent Engine（ReasoningEngine）を1つ作成する一回限りの provision スクリプト。

このバックエンドは Agent Engine に「デプロイ」しない。作成した Agent Engine インスタンスを
**マネージドサービス**として2役で使う:
  ① marketing_agent のコード実行サンドボックスの親（sandboxes.execute_code を直接叩く。
     ADK の CodeExecutor は使わない＝ADR-009）
  ② 会話セッションのストア（VertexAiSessionService）

前提（事前に1回だけ実施）:
  1. Agent Platform / Vertex AI API を有効化:
       gcloud services enable aiplatform.googleapis.com --project <PROJECT>
  2. 実行サービスアカウント（Cloud Run の SA / ローカルは自分の ADC）に role を付与:
       gcloud projects add-iam-policy-binding <PROJECT> \
         --member="serviceAccount:<SA>" --role="roles/aiplatform.user"
  3. ADC を用意（ローカルなら `gcloud auth application-default login`）。

使い方:
    uv run python scripts/provision_agent_engine.py

出力された AGENT_ENGINE_RESOURCE_NAME / AGENT_ENGINE_ID を backend/.env に貼る。
リージョンは Agent Engine のサポート地域（us-central1 等）。Gemini 呼び出しの global とは別。
"""

import os
import sys

import vertexai

# backend/ をインポートパスに追加して config を再利用
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    project = settings.google_cloud_project
    location = settings.agent_runtime_location

    print(f"Creating Agent Engine: project={project} location={location} ...")
    client = vertexai.Client(project=project, location=location)
    agent_engine = client.agent_engines.create(
        config={
            "display_name": "eventtune-runtime",
            "description": "Code execution sandbox + session store for marketing_agent",
        }
    )
    resource_name = agent_engine.api_resource.name
    engine_id = resource_name.split("/")[-1]

    print("\n=== 作成完了。以下を backend/.env に設定してください ===")
    print(f"AGENT_ENGINE_RESOURCE_NAME={resource_name}")
    print(f"AGENT_ENGINE_ID={engine_id}")
    print(f"AGENT_RUNTIME_LOCATION={location}")


if __name__ == "__main__":
    main()
