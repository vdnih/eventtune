import json
import re
import uuid
from typing import Any

import pandas as pd
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from contents import CONTENTS
from jobs import jobs

BATCH_SIZE = 10


def _build_instruction() -> str:
    contents_list = "\n".join(
        f"- 【{c['type']}】{c['name']}: {c['description']} (URL: {c['url']})"
        for c in CONTENTS
    )
    return f"""あなたは展示会後のフォローメール専門家です。
担当者の名刺情報・商談メモ・課題などを読み取り、以下のコンテンツリストから最も適切なものを1つ選び、
個別メッセージを日本語で生成してください。

## 案内できるコンテンツ一覧
{contents_list}

## 個別メッセージのフォーマット
「〇〇のような課題をお持ちの方に、[コンテンツ名]をご紹介します。[具体的な価値・内容の説明（1〜2文）]」
文字数の目安: 100〜150文字

## 回答形式
必ずJSON配列のみで回答してください。前置きや説明テキストは不要です。
[
  {{
    "row_index": <元のrow_index>,
    "content_type": "セミナー または イベント または 資料",
    "content_name": "コンテンツ名（上記リスト内のものを正確に使用）",
    "content_url": "コンテンツのURL（上記リスト内のものを正確に使用）",
    "message": "個別メッセージ"
  }}
]
"""


_agent = Agent(
    name="message_generator",
    model="gemini-3.1-flash-lite",
    instruction=_build_instruction(),
)

_session_service = InMemorySessionService()
_runner = Runner(
    agent=_agent,
    app_name="mmg",
    session_service=_session_service,
)


async def _run_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    batch_id = str(uuid.uuid4())[:8]
    session = await _session_service.create_session(
        app_name="mmg",
        user_id=f"batch_{batch_id}",
    )

    user_msg = (
        "以下の担当者リストについて、それぞれ最適なコンテンツを選び個別メッセージを生成してください:\n\n"
        + json.dumps(batch, ensure_ascii=False, indent=2)
    )
    content = types.Content(role="user", parts=[types.Part(text=user_msg)])

    response_text = ""
    async for event in _runner.run_async(
        user_id=f"batch_{batch_id}",
        session_id=session.id,
        new_message=content,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    response_text = part.text

    clean = re.sub(r"```(?:json)?\n?", "", response_text).strip().rstrip("`").strip()
    return json.loads(clean)


async def generate_messages(job_id: str, df: pd.DataFrame) -> None:
    job = jobs[job_id]
    records = df.to_dict("records")

    batches: list[list[dict]] = []
    for i in range(0, len(records), BATCH_SIZE):
        batch = [
            {
                "row_index": i + j,
                "data": {k: str(v) for k, v in row.items() if pd.notna(v)},
            }
            for j, row in enumerate(records[i : i + BATCH_SIZE])
        ]
        batches.append(batch)

    all_results: dict[int, dict] = {}

    for batch in batches:
        try:
            results = await _run_batch(batch)
            for r in results:
                all_results[r["row_index"]] = r
        except Exception as e:
            for item in batch:
                idx = item["row_index"]
                all_results[idx] = {
                    "row_index": idx,
                    "content_type": "",
                    "content_name": "",
                    "content_url": "",
                    "message": f"生成エラー: {str(e)[:80]}",
                }
        finally:
            job.done = min(job.done + len(batch), job.total)

    result_df = df.copy()
    result_df["案内コンテンツ種別"] = [
        all_results.get(i, {}).get("content_type", "") for i in range(len(df))
    ]
    result_df["案内コンテンツ名"] = [
        all_results.get(i, {}).get("content_name", "") for i in range(len(df))
    ]
    result_df["案内コンテンツURL"] = [
        all_results.get(i, {}).get("content_url", "") for i in range(len(df))
    ]
    result_df["個別メッセージ"] = [
        all_results.get(i, {}).get("message", "") for i in range(len(df))
    ]

    job.result_df = result_df
    job.status = "completed"
