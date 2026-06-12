import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from firebase_admin import firestore
from google import genai
from google.genai import types
from google.api_core.exceptions import ResourceExhausted

from contents_library import CONTENT_LIBRARY
from ontology import BlockType, LeadSegment, StructuredLead, TotalTailoredEmail

MODEL = "gemini-3.1-flash-lite"
CALL_INTERVAL = 2.0  # seconds between leads to avoid rate limiting
MAX_RETRIES = 4

logger = logging.getLogger(__name__)

_CONTENT_LIBRARY_JSON = json.dumps(
    [
        {
            "id": item.id,
            "content_type": item.content_type.value,
            "name": item.name,
            "description": item.description,
            "url": item.url,
        }
        for item in CONTENT_LIBRARY
    ],
    ensure_ascii=False,
    indent=2,
)

_SYSTEM_PROMPT = f"""\
あなたは展示会フォローメールの専門家です。
リード情報とコンテンツライブラリを元に、個別最適化されたメールを生成してください。

## コンテンツライブラリ
{_CONTENT_LIBRARY_JSON}

## ブロック選択ルール（リードのセグメント別）

### アポ獲得済み
- 1_展示会のお礼と挨拶: 必須
- 2_日程調整・候補日打診: 必須（具体的な候補日を提示する）
- 3_導入事例の紹介: 必須
- 4_プロダクト資料・ホワイトペーパーの紹介: 任意
- 5_未来の募集中のセミナー案内: 任意
- 6_結びの挨拶: 必須

### アポなし・感度高
- 1_展示会のお礼と挨拶: 必須
- 2_日程調整・候補日打診: 任意（軽いトーンで）
- 3_導入事例の紹介: 必須
- 4_プロダクト資料・ホワイトペーパーの紹介: 必須
- 5_未来の募集中のセミナー案内: 任意
- 6_結びの挨拶: 必須

### 通常リード
- 1_展示会のお礼と挨拶: 必須
- 2_日程調整・候補日打診: 含めない
- 3_導入事例の紹介: 任意
- 4_プロダクト資料・ホワイトペーパーの紹介: 必須
- 5_未来の募集中のセミナー案内: 必須
- 6_結びの挨拶: 必須

## 重要: Chain-of-Thought（reason_for_inclusion）
各ブロックについて、必ず reason_for_inclusion に「なぜこのブロックを含めるのか・\
どのコンテンツを選んだのか・なぜそのコンテンツが最適なのか」を日本語1〜2文で記述してから \
block_text を書いてください。これは思考の証跡として必須です。

## associated_content_ids
コンテンツライブラリから参照したコンテンツがある場合は、そのidを配列で指定してください。
例: ["seminar_02", "doc_03"]

## メール文体
- 件名は20〜40文字程度、具体的で開封したくなるもの
- 本文は丁寧なビジネス敬語（〜でございます調ではなく〜です・ます調）
- リードの課題・役職・会社名を自然に織り込む
- 1ブロック100〜200文字程度
"""


async def generate_email(lead: StructuredLead) -> TotalTailoredEmail:
    client = genai.Client()

    lead_json = json.dumps(
        {
            "name": lead.name,
            "company_name": lead.company_name,
            "department": lead.department,
            "job_title": lead.job_title,
            "segment": lead.segment.value,
            "interested_products": [p.value for p in lead.interested_products],
            "extracted_challenge": lead.extracted_challenge,
        },
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"以下のリード情報に基づいて、個別最適化されたメールを生成してください:\n\n{lead_json}"

    for attempt in range(MAX_RETRIES):
        try:
            response = await client.aio.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=TotalTailoredEmail,
                ),
            )
            return TotalTailoredEmail.model_validate_json(response.text)
        except ResourceExhausted as e:
            wait = 10 * (2 ** attempt)  # 10s, 20s, 40s, 80s
            logger.warning("429 on attempt %d/%d for %s, waiting %ds: %s", attempt + 1, MAX_RETRIES, lead.name, wait, e)
            if attempt + 1 < MAX_RETRIES:
                await asyncio.sleep(wait)
            else:
                raise


async def generate_emails_for_batch(batch_id: str) -> list[dict]:
    db = firestore.client()

    leads_snap = (
        db.collection("batches").document(batch_id).collection("leads").get()
    )
    if not leads_snap:
        return []

    batch_ref = db.collection("batches").document(batch_id)
    batch_ref.update({"execution_status": "running", "execution_done": 0})

    results = []
    for i, snap in enumerate(leads_snap):
        lead_data = snap.to_dict()
        lead_id = lead_data.get("lead_id", snap.id)

        lead = StructuredLead.model_validate(lead_data)
        logger.info("generating email %d/%d for %s (%s)", i + 1, len(leads_snap), lead.name, lead.segment.value)

        email = await generate_email(lead)

        email_id = str(uuid.uuid4())
        email_doc = {
            "email_id": email_id,
            "lead_id": lead_id,
            "batch_id": batch_id,
            "subject": email.subject,
            "blocks": [b.model_dump() for b in email.email_blocks],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        db.collection("emails").document(email_id).set(email_doc)

        results.append(email_doc)
        batch_ref.update({"execution_done": i + 1})

        if i + 1 < len(leads_snap):
            await asyncio.sleep(CALL_INTERVAL)

    batch_ref.update({
        "execution_status": "done",
        "email_count": len(results),
    })

    return results
