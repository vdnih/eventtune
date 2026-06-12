import json
import logging
import uuid
from datetime import datetime, timezone

import pandas as pd
from firebase_admin import firestore
from google import genai
from google.genai import types
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from ontology import LeadSegment, ProductSegment, StructuredLead

BATCH_SIZE = 15
MODEL = "gemini-3.1-flash-lite"


class _IngestionResponse(BaseModel):
    leads: list[StructuredLead]


_SYSTEM_PROMPT = """\
あなたは展示会・イベントのリード（見込み客）データ整理の専門家です。
以下のCSVデータ（列名が不統一・乱雑）から、各行について構造化リードを抽出してください。

## 抽出ルール

### name（氏名）
- 「姓」「名」が別列の場合は結合する（例: 「田中」+「修一」→「田中 修一」）
- 「担当者名」「氏名」「お名前」等の列を使う

### company_name（会社名）
- 「会社名」「法人名」「所属先」等の列を使う
- 表記はそのまま使用する

### department（部署名）
- 「部署」「部署名」「所属」等の列を使う
- 空欄・不明の場合は "不明" とする

### job_title（役職）
- 「役職」「肩書き」「ポジション」等の列を使う
- 空欄・不明の場合は "不明" とする

### segment（LeadSegment）— 以下のシグナルで判定する
判定値は次のいずれか: "アポ獲得済み" / "アポなし・感度高" / "通常リード"
- アポ獲得済み: 判定列=A かつ 温度感=高、または接客メモに「面談希望」「アポ」「商談確定」「来週」等が含まれる
- アポなし・感度高: 判定列=B または 温度感=中〜高、または資料請求・見積もり依頼が明確
- 通常リード: 判定列=C、名刺交換のみ、ノベルティ目当て、または温度感=低
- 判定が不明な場合は "通常リード" とする

### interested_products（List[ProductSegment]）
判定値は次のいずれか（複数可）: "プロダクトA" / "プロダクトB"
- プロダクトA: メモや判定列に「スキルマップ」「技能伝承」「アーカイブ」「育成」「人材育成」が含まれる
- プロダクトB: メモや判定列に「要員配置」「シフト」「シミュレーター」「資格管理」「安全講習」が含まれる
- 両方該当する場合は両方を返す
- 判断できない場合は ["プロダクトA"] をデフォルトとする

### extracted_challenge（抽出した課題）
- 「お悩み」「課題」「接客内容メモ」「ヒアリング内容」「要望」等の列から最も具体的な課題を1〜2文で要約する
- 空欄・「名刺交換のみ」等の場合は "課題不明" とする

## 重要な注意事項
- メールアドレスが壊れていても無視してリードを生成すること
- 行に空欄が多くても必ず出力すること（不明フィールドは "不明" で埋める）
- 全角記号・スペースが含まれていても正しく解釈すること
- 必ずすべての行について leads 配列に1件ずつ出力すること
"""


async def ingest_dataframe(
    df: pd.DataFrame,
    batch_id: str,
    filename: str,
) -> list[StructuredLead]:
    db = firestore.client()
    client = genai.Client()

    logger.info("ingest_dataframe start: batch_id=%s filename=%s rows=%d", batch_id, filename, len(df))
    db.collection("batches").document(batch_id).set({
        "filename": filename,
        "row_count": len(df),
        "status": "ingesting",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    records = df.to_dict("records")
    all_leads: list[StructuredLead] = []

    for i in range(0, len(records), BATCH_SIZE):
        chunk = records[i : i + BATCH_SIZE]
        batch_data = [
            {
                "row_index": i + j,
                "data": {k: str(v) for k, v in row.items() if pd.notna(v)},
            }
            for j, row in enumerate(chunk)
        ]

        logger.info("ingestion batch %d-%d / %d: calling Gemini...", i, i + len(chunk), len(records))
        prompt = (
            "以下のCSVデータを構造化してください:\n\n"
            + json.dumps(batch_data, ensure_ascii=False, indent=2)
        )

        response = await client.aio.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=_IngestionResponse,
            ),
        )

        logger.info("ingestion batch %d: got response, parsing...", i)
        result = _IngestionResponse.model_validate_json(response.text)
        all_leads.extend(result.leads)
        logger.info("ingestion batch %d: %d leads parsed (total so far: %d)", i, len(result.leads), len(all_leads))

    logger.info("ingestion complete: %d leads, saving to Firestore...", len(all_leads))
    batch_ref = db.collection("batches").document(batch_id)
    leads_ref = batch_ref.collection("leads")

    for lead in all_leads:
        lead_id = str(uuid.uuid4())
        leads_ref.document(lead_id).set({
            "lead_id": lead_id,
            **lead.model_dump(),
        })

    batch_ref.update({"status": "done", "lead_count": len(all_leads)})
    logger.info("ingestion saved: batch_id=%s lead_count=%d", batch_id, len(all_leads))

    return all_leads
