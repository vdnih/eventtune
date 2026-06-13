"""
MarketingAgent — Layer 3: マーケティングエージェント

単一・汎用のエージェント。システムプロンプトには:
  - プラットフォームの思想
  - オントロジーの定義
  - 利用可能なツールの一覧と用途

のみを記述する。タスク別の手順は事前定義しない。
ユーザーの指示に対して、プロのマーケターとして自律的に判断・行動する。
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from firebase_admin import firestore
from google import genai
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from ontology import (
    CostCategory,
    CostSummary,
    ComposedEmail,
    Contact,
    ContentAsset,
    EmailBlock,
    Event,
    EventKPI,
    SurveyResponse,
)

logger = logging.getLogger(__name__)

_MODEL = "gemini-3.1-flash-lite"

# ── システムプロンプト ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
あなたはイベントマーケティングAIプラットフォームのマーケティングエージェントです。

【プラットフォームの思想】
このプラットフォームは、展示会・セミナー・イベントを中心に、カオスなマーケティングデータを
オントロジーに統合し、AIエージェントがマーケティング活動を行うための基盤です。

設計原則:
- Event-Centric: すべてのデータは「どのイベントで」という文脈を持つ
- Ontology-First: データは必ずオントロジーへのマッピングを経由する
- Auditable AI: すべてのAI判断には根拠が必要。reason_for_inclusion は Optional 不可
- Historical Intelligence: イベントをまたいだ蓄積・比較・学習が価値を持つ

【オントロジーの定義】
- Event: 展示会・セミナー・イベントの記録。KPI・費用・参加者を持つ
- Contact: 人物（ハウスリストの連絡先）。ContactStage と EngagementLevel を持つ
  - ContactStage: LEAD / MQL / SQL / CUSTOMER / EXCLUDED
  - EngagementLevel (stage=LEAD のとき有効): アポ獲得済み / アポなし・感度高 / 通常リード
- EventKPI: イベントの定量成果（来場者数・アポ数・パイプライン額など）
- SurveyResponse: 参加者アンケート集計（NPS・満足度・バーバティムコメント）
- CostItem: イベントの費用明細
- ContentAsset: 推薦可能なコンテンツ（資料・事例・セミナー等）
- ComposedEmail: AIが生成したメール。EmailBlock のリストで構成される
  - EmailBlock.reason_for_inclusion: そのブロックを含めた理由（必須・非null）

【あなたの役割】
プロのマーケターとして、ユーザーの指示を遂行してください。
進め方・手順はあなた自身が判断してください。
ツールを組み合わせて作業を完遂し、結果を分かりやすく日本語で報告してください。

ROI計算が必要な場合の公式:
  roi_pipeline = (pipeline_value_jpy / total_cost_jpy) × 100 (%)
  cost_per_contact = total_cost_jpy / total_contacts_collected
  cost_per_appointment = total_cost_jpy / appointments_booked

メールを生成する場合:
  - 各 EmailBlock に reason_for_inclusion（そのブロックを選んだ理由）を必ず含める
  - コンタクトの課題・役職・EngagementLevel に応じて内容を個別最適化する
  - block_type に日本語で簡潔なブロック名を設定する
"""


# ── ツール定義 ────────────────────────────────────────────────────────────────

def _db() -> Any:
    return firestore.client()


def list_events() -> str:
    """登録されているすべてのイベントの一覧を取得する。"""
    docs = _db().collection("events").get()
    events = [d.to_dict() for d in docs]
    return json.dumps(events, ensure_ascii=False, default=str)


def get_event_detail(event_id: str) -> str:
    """指定したイベントの詳細情報を取得する。"""
    doc = _db().collection("events").document(event_id).get()
    if not doc.exists:
        return json.dumps({"error": f"event_id '{event_id}' not found"})
    return json.dumps(doc.to_dict(), ensure_ascii=False, default=str)


def get_event_contacts(event_id: str, engagement_level: str = "") -> str:
    """
    指定したイベントのコンタクト一覧を取得する。
    engagement_level を指定するとフィルタリングできる（例: "アポ獲得済み"）。
    """
    db = _db()
    # NOTE: batches/{bid} ドキュメントは取り込み時に実体化されない（contacts のみ書き込む）
    # ため、コレクションクエリ .get() では祖先パスの幽霊ドキュメントを拾えない。
    # 幽霊ドキュメントも含めて列挙する .list_documents() を使う。
    batches = db.collection(f"events/{event_id}/batches").list_documents()
    contacts = []
    for batch in batches:
        coll = db.collection(f"events/{event_id}/batches/{batch.id}/contacts").get()
        for c in coll:
            data = c.to_dict()
            if not engagement_level or data.get("engagement_level") == engagement_level:
                contacts.append(data)
    return json.dumps(contacts, ensure_ascii=False, default=str)


def get_event_kpi(event_id: str) -> str:
    """指定したイベントの KPI データを取得する。"""
    docs = _db().collection(f"events/{event_id}/kpi").get()
    kpis = [d.to_dict() for d in docs]
    return json.dumps(kpis[0] if kpis else {}, ensure_ascii=False, default=str)


def get_event_survey(event_id: str) -> str:
    """指定したイベントのアンケート集計データを取得する。"""
    docs = _db().collection(f"events/{event_id}/survey").get()
    surveys = [d.to_dict() for d in docs]
    return json.dumps(surveys[0] if surveys else {}, ensure_ascii=False, default=str)


def get_event_costs(event_id: str) -> str:
    """
    指定したイベントの費用明細とカテゴリ別集計（CostSummary）を取得する。
    返却: { costs: [...], summary: { total_jpy: ..., by_category: {...} } }
    """
    docs = _db().collection(f"events/{event_id}/costs").get()
    costs = [d.to_dict() for d in docs]
    total = sum(c.get("amount_jpy", 0) for c in costs)
    by_category: dict[str, float] = {}
    for c in costs:
        cat = c.get("category", "その他")
        by_category[cat] = by_category.get(cat, 0) + c.get("amount_jpy", 0)
    summary = CostSummary(total_jpy=total, by_category=by_category)
    return json.dumps(
        {"costs": costs, "summary": summary.model_dump()},
        ensure_ascii=False,
        default=str,
    )


def get_all_events_summary() -> str:
    """
    すべてのイベントを横断した比較サマリーを返す。
    振り返り・比較分析に使用する。
    """
    db = _db()
    events = [d.to_dict() for d in db.collection("events").get()]
    summaries = []
    for ev in events:
        eid = ev.get("event_id", "")
        kpi_docs = db.collection(f"events/{eid}/kpi").get()
        kpi = kpi_docs[0].to_dict() if kpi_docs else {}
        cost_docs = db.collection(f"events/{eid}/costs").get()
        total_cost = sum(c.to_dict().get("amount_jpy", 0) for c in cost_docs)
        survey_docs = db.collection(f"events/{eid}/survey").get()
        survey = survey_docs[0].to_dict() if survey_docs else {}

        roi_pipeline = 0.0
        if total_cost > 0 and kpi.get("pipeline_value_jpy", 0) > 0:
            roi_pipeline = round(kpi["pipeline_value_jpy"] / total_cost * 100, 1)

        summaries.append({
            "event_id": eid,
            "event_name": ev.get("name", ""),
            "event_date": ev.get("event_date", ""),
            "total_contacts": kpi.get("total_contacts_collected", 0),
            "appointments_booked": kpi.get("appointments_booked", 0),
            "roi_pipeline": roi_pipeline,
            "total_cost_jpy": total_cost,
            "pipeline_value_jpy": kpi.get("pipeline_value_jpy", 0),
            "nps_score": survey.get("nps_score", None),
        })
    return json.dumps(summaries, ensure_ascii=False, default=str)


def get_content_catalog() -> str:
    """推薦可能なコンテンツ資産（資料・事例・セミナー等）の一覧を取得する。"""
    docs = _db().collection("content_assets").get()
    assets = [d.to_dict() for d in docs]
    return json.dumps(assets, ensure_ascii=False, default=str)


def save_report(event_id: str, report_type: str, content: str) -> str:
    """
    分析レポートや戦略提案を Firestore に保存する。
    report_type: "retrospective" / "strategy" / その他の自由な文字列
    content: JSON 文字列または自由テキスト
    """
    report_id = f"report_{uuid.uuid4().hex[:12]}"
    _db().collection(f"events/{event_id}/reports").document(report_id).set({
        "report_id": report_id,
        "event_id": event_id,
        "report_type": report_type,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return json.dumps({"report_id": report_id, "status": "saved"})


def compose_emails(
    contact_ids: list[str],
    purpose: str,
    context: str = "",
    content_asset_ids: list[str] = [],
) -> str:
    """
    指定したコンタクトに対してメールを生成し、marketing_runs に保存する。

    Args:
        contact_ids: 対象コンタクトの ID リスト
        purpose: メールの目的（例: "展示会フォローアップ", "セミナー案内", "製品アップデート通知"）
        context: 追加の指示・背景情報（自由テキスト）
        content_asset_ids: 参照させたいコンテンツ資産の asset_id リスト

    Returns:
        { run_id, total, status }
    """
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    _db().collection("marketing_runs").document(run_id).set({
        "run_id": run_id,
        "status": "queued",
        "purpose": purpose,
        "context": context,
        "contact_ids": contact_ids,
        "content_asset_ids": content_asset_ids,
        "total": len(contact_ids),
        "done": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # 実行はフロントエンドが POST /api/marketing/runs/{run_id}/execute を呼ぶことで開始する
    return json.dumps({"run_id": run_id, "total": len(contact_ids), "status": "queued"})


def export_emails_csv(run_id: str) -> str:
    """指定した run_id のメール生成結果を CSV 形式で返す。"""
    db = _db()
    run_doc = db.collection("marketing_runs").document(run_id).get()
    if not run_doc.exists:
        return json.dumps({"error": f"run_id '{run_id}' not found"})
    emails = [d.to_dict() for d in db.collection(f"marketing_runs/{run_id}/emails").get()]
    if not emails:
        return json.dumps({"error": "no emails found for this run"})
    # CSV 文字列を生成
    import csv, io
    buf = io.StringIO()
    fieldnames = ["contact_id", "subject", "full_text"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for email in emails:
        blocks = email.get("blocks", [])
        full_text = "\n\n".join(b.get("block_text", "") for b in blocks)
        writer.writerow({
            "contact_id": email.get("contact_id", ""),
            "subject": email.get("subject", ""),
            "full_text": full_text,
        })
    return json.dumps({"csv": buf.getvalue(), "count": len(emails)})


# ── エージェント・ランナー構築 ────────────────────────────────────────────────

_TOOLS = [
    list_events,
    get_event_detail,
    get_event_contacts,
    get_event_kpi,
    get_event_survey,
    get_event_costs,
    get_all_events_summary,
    get_content_catalog,
    save_report,
    compose_emails,
    export_emails_csv,
]

_agent = Agent(
    name="marketing_agent",
    model=_MODEL,
    description="イベントマーケティングAIエージェント。メール生成・振り返り分析・戦略立案を汎用的に担う。",
    instruction=_SYSTEM_PROMPT,
    tools=_TOOLS,
)

_session_service = InMemorySessionService()
_APP_NAME = "event_marketing_platform"


async def chat_stream(
    message: str,
    session_id: str,
    user_id: str = "default_user",
) -> AsyncGenerator[dict, None]:
    """
    MarketingAgent とのチャットを SSE 用のイベント辞書としてストリーミングする。

    Yields:
        { type: "tool_call" | "text" | "done" | "error", ... }
    """
    runner = Runner(
        agent=_agent,
        app_name=_APP_NAME,
        session_service=_session_service,
    )

    # セッション初期化（既存があれば再利用、無ければ作成）
    # InMemorySessionService.get_session は未存在時に None を返す（例外を投げない）ため
    # None チェックで明示的に作成する。
    session = await _session_service.get_session(
        app_name=_APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        session = await _session_service.create_session(
            app_name=_APP_NAME, user_id=user_id, session_id=session_id
        )

    user_content = types.Content(
        role="user",
        parts=[types.Part(text=message)],
    )

    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
        ):
            # ToolCall イベント
            if event.get_function_calls():
                for fc in event.get_function_calls():
                    yield {
                        "type": "tool_call",
                        "tool_name": fc.name,
                        "args": dict(fc.args) if fc.args else {},
                    }
            # テキスト応答
            elif event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        yield {"type": "text", "text": part.text}

        yield {"type": "done"}

    except Exception as e:
        logger.exception("chat_stream error: session_id=%s", session_id)
        yield {"type": "error", "message": str(e)}


# ── メール生成バックグラウンドジョブ ─────────────────────────────────────────

async def _execute_email_run(run_id: str) -> None:
    """
    compose_emails ツールで作成された run を実際に実行する。
    contacts を Firestore から取得し、各コンタクトへのメールを生成して保存する。
    """
    db = _db()
    run_doc = db.collection("marketing_runs").document(run_id).get()
    if not run_doc.exists:
        return
    run = run_doc.to_dict()

    contact_ids: list[str] = run.get("contact_ids", [])
    purpose: str = run.get("purpose", "")
    context: str = run.get("context", "")
    content_asset_ids: list[str] = run.get("content_asset_ids", [])

    db.collection("marketing_runs").document(run_id).update({"status": "running"})

    # ContentAsset を取得
    assets = []
    for asset_id in content_asset_ids:
        doc = db.collection("content_assets").document(asset_id).get()
        if doc.exists:
            assets.append(doc.to_dict())

    client = genai.Client()
    email_system_prompt = _build_email_system_prompt(purpose, context, assets)

    class _EmailSchema(ComposedEmail):
        pass

    done = 0
    for contact_id in contact_ids:
        # コンタクトを全イベントから検索
        contact_data = _find_contact(db, contact_id)
        if not contact_data:
            logger.warning("contact not found: %s", contact_id)
            continue

        contact_json = json.dumps(contact_data, ensure_ascii=False, default=str)
        prompt = f"以下のコンタクト情報に基づいてメールを生成してください:\n\n{contact_json}"

        try:
            response = await client.aio.models.generate_content(
                model=_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=email_system_prompt,
                    response_mime_type="application/json",
                    response_schema=_EmailSchema,
                ),
            )
            email = _EmailSchema.model_validate_json(response.text)
        except Exception as e:
            logger.exception("email generation failed for contact %s: %s", contact_id, e)
            continue

        email_id = f"email_{uuid.uuid4().hex[:12]}"
        email_doc = {
            **email.model_dump(),
            "email_id": email_id,
            "contact_id": contact_id,
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        db.collection(f"marketing_runs/{run_id}/emails").document(email_id).set(email_doc)

        done += 1
        db.collection("marketing_runs").document(run_id).update({"done": done})

        # レート制限回避
        if done < len(contact_ids):
            await asyncio.sleep(1.5)

    db.collection("marketing_runs").document(run_id).update(
        {"status": "done", "email_count": done}
    )
    logger.info("email run completed: run_id=%s done=%d", run_id, done)


def _build_email_system_prompt(purpose: str, context: str, assets: list[dict]) -> str:
    assets_text = json.dumps(assets, ensure_ascii=False, indent=2) if assets else "（なし）"
    return f"""\
あなたはプロのマーケターです。
以下の目的・背景に基づき、コンタクト情報を読んでパーソナライズされたメールを生成してください。

【メールの目的】
{purpose}

【追加の背景・指示】
{context if context else "（なし）"}

【参照するコンテンツ資産】
{assets_text}

【必須ルール】
- 各 EmailBlock に reason_for_inclusion（そのブロックを選んだ理由）を必ず記述する
- コンタクトの課題・役職・EngagementLevel に応じて内容を個別最適化する
- 件名は20〜40文字程度、具体的で開封したくなるもの
- 本文はビジネス敬語（〜です・ます調）
- 1ブロック100〜200文字程度
- associated_asset_ids: 参照したコンテンツ資産の asset_id を設定する
"""


def _find_contact(db: Any, contact_id: str) -> dict | None:
    """全イベントのバッチからコンタクトを検索する。"""
    events = db.collection("events").get()
    for ev in events:
        # batches/{bid} は実体化されない幽霊ドキュメントのため list_documents() で列挙する
        # （get_event_contacts と同じ理由）。
        batches = db.collection(f"events/{ev.id}/batches").list_documents()
        for batch in batches:
            doc = db.document(
                f"events/{ev.id}/batches/{batch.id}/contacts/{contact_id}"
            ).get()
            if doc.exists:
                return doc.to_dict()
    return None
