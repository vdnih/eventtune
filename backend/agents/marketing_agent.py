"""
MarketingAgent — Layer 3: マーケティングエージェント

単一・汎用のエージェント。システムプロンプトには:
  - プラットフォームの思想
  - オントロジーの定義
  - 利用可能なツールの一覧と用途

のみを記述する。タスク別の手順は事前定義しない。
ユーザーの指示に対して、プロのマーケターとして自律的に判断・行動する。

【マルチテナント: AI非依存のスペース束縛】
ツールはモジュールグローバルではなく make_tools(db) のファクトリで生成し、スペースで
前置済みの ScopedClient を closure で捕捉する。ツールのシグネチャに space_id は存在せず、
AI は他スペースを名指しする経路を持たない（最小権限の構造的強制）。Agent はリクエストごとに
build_agent(db) で構築する。詳細は docs/PHILOSOPHY_AND_NAMING.md。
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from google import genai
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from metering import metered, record_compute, record_llm, record_llm_response
from ontology import (
    CostSummary,
    ComposedEmail,
    EmailBlock,
    Segment,
    SegmentAxis,
)
from segmentation import assign_contacts_to_segment
from space import SpaceContext

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
プロのマーケターとして、ユーザーの曖昧な意図を読み解き、進め方をあなた自身が組み立てて
遂行してください。手順は固定されていません。状況に応じてツールを組み合わせてください。
結果は分かりやすい日本語で、判断の根拠とともに報告してください。

ROI計算が必要な場合の公式:
  roi_pipeline = (pipeline_value_jpy / total_cost_jpy) × 100 (%)
  cost_per_contact = total_cost_jpy / total_contacts_collected
  cost_per_appointment = total_cost_jpy / appointments_booked

【個別カスタマイズ（メール等）の進め方 — セグメント方式 + HIL】
全コンタクトに1通ずつフル生成するのではなく、少数のセグメントに切り分け、セグメント単位で
コンテンツのパターンを作り、各コンタクトのメールは決定論的に組み立てます（高速・低コスト）。

あなたが自律的に進める標準の流れ（ただし各ゲートで必ず確認を取ること = Human-In-the-Loop）:
  1. 対象を特定し（get_event_* 等）、コンタクトの分布を踏まえて**適切なセグメント軸を自分で設計**する
     （例: 課題感 × 購買意欲）。→ 「この軸で切り分けます。よろしいですか？」と提案し**承認を待つ**。
  2. 承認後に define_segment でセグメントを登録し、assign_segment で分類する。
     → 各バケットの人数と分類根拠の例を提示し、「この分類で進めます。修正は？」と**確認する**。
  3. 承認後に generate_patterns でバケットごとのコンテンツパターンを生成する。
     → 生成パターンを提示し、「この文面で全件組み立てます。よいですか？」と**確認する**。
  4. **明示的な承認を得てから** run_assembly を呼び、全件を組み立てる。完了後 run_id を伝える。

重要: 提案や確認を飛ばして確定ツール（assign_segment / generate_patterns / run_assembly）を
呼ばないこと。とくに run_assembly（全件確定）はユーザーの明示承認なしに実行してはならない。
ユーザーが軌道修正（軸の変更・対象の絞り込み・文面トーンの変更など）を求めたら、該当ステップを
やり直してから次へ進むこと。
"""


# ── ツール定義（スペース束縛ファクトリ） ──────────────────────────────────────
#
# db は space.ScopedClient（spaces/{space_id}/ で前置済み）。各ツールは db を closure で
# 捕捉するため、自スペース配下にしか到達できない。ツール引数に space_id は存在しない。
# space は計測（メータリング）専用に捕捉する（データ参照は db のみを使う）。

def make_tools(db: Any, space: SpaceContext) -> list:
    """スペース前置済み db を closure 束縛したツール群を返す。"""

    def list_events() -> str:
        """登録されているすべてのイベントの一覧を取得する。"""
        docs = db.collection("events").get()
        events = [d.to_dict() for d in docs]
        return json.dumps(events, ensure_ascii=False, default=str)

    def get_event_detail(event_id: str) -> str:
        """指定したイベントの詳細情報を取得する。"""
        doc = db.collection("events").document(event_id).get()
        if not doc.exists:
            return json.dumps({"error": f"event_id '{event_id}' not found"})
        return json.dumps(doc.to_dict(), ensure_ascii=False, default=str)

    def get_event_contacts(event_id: str, engagement_level: str = "") -> str:
        """
        指定したイベントのコンタクト一覧を取得する。
        engagement_level を指定するとフィルタリングできる（例: "アポ獲得済み"）。
        """
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
        docs = db.collection(f"events/{event_id}/kpi").get()
        kpis = [d.to_dict() for d in docs]
        return json.dumps(kpis[0] if kpis else {}, ensure_ascii=False, default=str)

    def get_event_survey(event_id: str) -> str:
        """指定したイベントのアンケート集計データを取得する。"""
        docs = db.collection(f"events/{event_id}/survey").get()
        surveys = [d.to_dict() for d in docs]
        return json.dumps(surveys[0] if surveys else {}, ensure_ascii=False, default=str)

    def get_event_costs(event_id: str) -> str:
        """
        指定したイベントの費用明細とカテゴリ別集計（CostSummary）を取得する。
        返却: { costs: [...], summary: { total_jpy: ..., by_category: {...} } }
        """
        docs = db.collection(f"events/{event_id}/costs").get()
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
        docs = db.collection("content_assets").get()
        assets = [d.to_dict() for d in docs]
        return json.dumps(assets, ensure_ascii=False, default=str)

    def save_report(event_id: str, report_type: str, content: str) -> str:
        """
        分析レポートや戦略提案を Firestore に保存する。
        report_type: "retrospective" / "strategy" / その他の自由な文字列
        content: JSON 文字列または自由テキスト
        """
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        db.collection(f"events/{event_id}/reports").document(report_id).set({
            "report_id": report_id,
            "event_id": event_id,
            "report_type": report_type,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return json.dumps({"report_id": report_id, "status": "saved"})

    # ── 個別カスタマイズ（セグメント方式・HIL） ──────────────────────────────

    def define_segment(
        name: str,
        purpose: str,
        axes_json: str,
        buckets: list[str],
        criteria: str,
    ) -> str:
        """
        施策向けのセグメント軸を設計し、オントロジーに登録する（HILゲート①の承認後に呼ぶ）。

        Args:
            name: 施策名（例: "2026春展示会フォローアップ"）
            purpose: 施策の目的（パターン生成に渡る）
            axes_json: 軸定義のJSON文字列。例 '[{"name":"課題感","values":["高","中","低"]},
                       {"name":"購買意欲","values":["高","低"]}]'
            buckets: 運用単位のセグメント値（直積セル等）。例 ["高課題×高意欲","高課題×低意欲",...]
            criteria: 各バケットへの割り当て基準（自然言語）

        Returns:
            { segment_id, name, buckets }
        """
        try:
            axes_raw = json.loads(axes_json)
            axes = [SegmentAxis(name=a["name"], values=list(a["values"])) for a in axes_raw]
        except Exception as e:
            return json.dumps({"error": f"axes_json の解析に失敗: {e}"})

        segment_id = f"seg_{uuid.uuid4().hex[:12]}"
        segment = Segment(
            segment_id=segment_id,
            name=name,
            purpose=purpose,
            axes=axes,
            buckets=list(buckets),
            criteria=criteria,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.collection("segments").document(segment_id).set(segment.model_dump())
        return json.dumps(
            {"segment_id": segment_id, "name": name, "buckets": list(buckets)},
            ensure_ascii=False,
        )

    def assign_segment(segment_id: str, event_id: str = "") -> str:
        """
        登録済みセグメントに従って対象コンタクトを各バケットへ分類する（HILゲート①承認後）。
        event_id を指定するとそのイベントのコンタクトのみ、未指定なら全コンタクトが対象。

        構造化フィールドで自明な分は決定論、意味判断が要る分のみ軽量モデルで判別する。
        各割り当てには根拠（reason）が残る。

        Returns:
            { total, by_bucket, llm_contacts }（人数分布と分類根拠の概況）
        """
        doc = db.collection("segments").document(segment_id).get()
        if not doc.exists:
            return json.dumps({"error": f"segment_id '{segment_id}' not found"})
        segment = Segment.model_validate(doc.to_dict())
        with metered(space):
            summary = assign_contacts_to_segment(db, space, segment, event_id or None)
        return json.dumps(summary, ensure_ascii=False)

    def generate_patterns(
        segment_id: str,
        purpose: str = "",
        context: str = "",
        content_asset_ids: list[str] = [],
    ) -> str:
        """
        バケットごとに1つずつコンテンツパターン（件名＋本文ブロック）を生成する（HILゲート②承認後）。
        コスト = バケット数ぶんのLLM呼び出しのみ。生成後はユーザーにレビューさせること。

        本文中の個人差分は {name} {company_name} {department} {job_title} のプレースホルダで表現する。

        Returns:
            { segment_id, patterns: [{bucket, subject}], count }
        """
        doc = db.collection("segments").document(segment_id).get()
        if not doc.exists:
            return json.dumps({"error": f"segment_id '{segment_id}' not found"})
        segment = Segment.model_validate(doc.to_dict())

        assets = []
        for asset_id in content_asset_ids:
            a = db.collection("content_assets").document(asset_id).get()
            if a.exists:
                assets.append(a.to_dict())

        eff_purpose = purpose or segment.purpose
        results = []
        with metered(space):
            for bucket in segment.buckets:
                pattern = _generate_one_pattern(space, segment, bucket, eff_purpose, context, assets)
                db.collection(f"segments/{segment_id}/patterns").document(bucket).set(pattern)
                results.append({"bucket": bucket, "subject": pattern.get("subject", "")})
        return json.dumps(
            {"segment_id": segment_id, "patterns": results, "count": len(results)},
            ensure_ascii=False,
        )

    def run_assembly(segment_id: str) -> str:
        """
        セグメントの分類とパターンから、各コンタクトのメールを決定論的に組み立てる（HILゲート③の
        明示承認後にのみ呼ぶ）。LLMは使わずプレースホルダ置換で組み立てるため高速。
        結果は marketing_runs に保存し、CSVは GET /api/marketing/runs/{run_id}/export で取得できる。

        Returns:
            { run_id, count }
        """
        doc = db.collection("segments").document(segment_id).get()
        if not doc.exists:
            return json.dumps({"error": f"segment_id '{segment_id}' not found"})
        segment = Segment.model_validate(doc.to_dict())

        # パターン未生成チェック
        patterns = {
            p.id: p.to_dict()
            for p in db.collection(f"segments/{segment_id}/patterns").get()
        }
        if not patterns:
            return json.dumps({"error": "パターンが未生成です。先に generate_patterns を実行してください"})

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        count = _assemble_run(db, segment, patterns, run_id)
        return json.dumps({"run_id": run_id, "count": count}, ensure_ascii=False)

    return [
        list_events,
        get_event_detail,
        get_event_contacts,
        get_event_kpi,
        get_event_survey,
        get_event_costs,
        get_all_events_summary,
        get_content_catalog,
        save_report,
        define_segment,
        assign_segment,
        generate_patterns,
        run_assembly,
    ]


# ── エージェント・ランナー構築 ────────────────────────────────────────────────

def build_agent(db: Any, space: SpaceContext) -> Agent:
    """スペース束縛ツールを持つ Agent をリクエストごとに構築する。"""
    return Agent(
        name="marketing_agent",
        model=_MODEL,
        description="イベントマーケティングAIエージェント。メール生成・振り返り分析・戦略立案を汎用的に担う。",
        instruction=_SYSTEM_PROMPT,
        tools=make_tools(db, space),
    )


_session_service = InMemorySessionService()
_APP_NAME = "event_marketing_platform"


def _accumulate_usage(event: Any, totals: dict[str, int]) -> None:
    """ADK イベントから usage_metadata を拾って累積する（防御的）。"""
    usage = getattr(event, "usage_metadata", None)
    if usage is None:
        return
    totals["input"] += getattr(usage, "prompt_token_count", 0) or 0
    totals["output"] += getattr(usage, "candidates_token_count", 0) or 0


async def chat_stream(
    message: str,
    session_id: str,
    space: SpaceContext,
    event_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    MarketingAgent とのチャットを SSE 用のイベント辞書としてストリーミングする。

    Yields:
        { type: "tool_call" | "text" | "done" | "error", ... }
    """
    db = space.scoped_db()
    # ADKセッションキーをスペースで名前空間化し、スペース間のセッション混線を防ぐ
    session_user_id = f"{space.space_id}:{space.uid}"

    runner = Runner(
        agent=build_agent(db, space),
        app_name=_APP_NAME,
        session_service=_session_service,
    )

    # セッション初期化（既存があれば再利用、無ければ作成）
    session = await _session_service.get_session(
        app_name=_APP_NAME, user_id=session_user_id, session_id=session_id
    )
    if session is None:
        session = await _session_service.create_session(
            app_name=_APP_NAME, user_id=session_user_id, session_id=session_id
        )

    # 選択中イベントがあれば、メッセージ先頭に文脈ブロックを前置する。
    # これによりエージェントは list_events での推測を省き、対象イベントの
    # get_event_* 系ツールへ直接 event_id を渡せる。未選択時は従来どおり全体が対象。
    message_text = message
    if event_id:
        event_name = event_id
        try:
            doc = db.collection("events").document(event_id).get()
            if doc.exists:
                event_name = doc.to_dict().get("name", event_id)
        except Exception:
            logger.warning("failed to load event context: event_id=%s", event_id)
        message_text = (
            f"[コンテキスト] ユーザーは現在「{event_name}」(event_id={event_id})を選択中です。"
            "特定イベントへの問い合わせは、明示が無い限りこのイベントを対象として "
            f"get_event_* 系ツールに event_id={event_id} を渡してください。\n\n"
            f"ユーザーの質問: {message}"
        )

    user_content = types.Content(
        role="user",
        parts=[types.Part(text=message_text)],
    )

    usage_totals = {"input": 0, "output": 0}
    start = time.monotonic()
    try:
        async for event in runner.run_async(
            user_id=session_user_id,
            session_id=session_id,
            new_message=user_content,
        ):
            _accumulate_usage(event, usage_totals)
            # ToolCall イベント
            if event.get_function_calls():
                for fc in event.get_function_calls():
                    yield {
                        "type": "tool_call",
                        "tool_name": fc.name,
                        "args": dict(fc.args) if fc.args else {},
                    }
            # ToolResponse イベント（run_id / segment_id 等の成果物をフロントへ）
            elif event.get_function_responses():
                for fr in event.get_function_responses():
                    yield {
                        "type": "tool_result",
                        "tool_name": fr.name,
                        "result": fr.response if isinstance(fr.response, dict) else {"value": fr.response},
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
    finally:
        # メータリング: LLMトークンとコンピュート実行時間を記録
        record_llm(space, _MODEL, usage_totals["input"], usage_totals["output"])
        record_compute(space, int((time.monotonic() - start) * 1000))


# ── Stage 2a: バケット単位のコンテンツパターン生成 ───────────────────────────
#
# 文体ルールの唯一の情報源。全コンタクトへのフル生成はせず、バケットごとに1パターンのみ
# 生成する。本文中の個人差分はプレースホルダで表現し、組み立て時に決定論で置換する。

# プレースホルダとして使える Contact フィールド
_PLACEHOLDER_FIELDS = ("name", "company_name", "department", "job_title")


class _PatternBlock(BaseModel):
    block_type: str
    reason_for_inclusion: str
    associated_asset_ids: list[str] = []
    block_text: str          # {name} {company_name} {department} {job_title} を含めてよい


class _PatternSchema(BaseModel):
    subject: str             # プレースホルダ可
    blocks: list[_PatternBlock]


def _generate_one_pattern(
    space: SpaceContext,
    segment: Segment,
    bucket: str,
    purpose: str,
    context: str,
    assets: list[dict],
) -> dict:
    """1バケットぶんのコンテンツパターンを生成して dict で返す。"""
    assets_text = json.dumps(assets, ensure_ascii=False, indent=2) if assets else "（なし）"
    system_prompt = f"""\
あなたはプロのマーケターです。施策「{segment.name}」のために、あるセグメントへ送る
メールの**ひな型（パターン）**を1つ作成してください。個々人に1通ずつではなく、この
セグメントに共通して使えるテンプレートを作ります。

【施策の目的】
{purpose}

【このパターンの対象セグメント】
{bucket}

【追加の背景・指示】
{context if context else "（なし）"}

【参照するコンテンツ資産】
{assets_text}

【必須ルール】
- 宛名や会社名など個人ごとに変わる箇所は、プレースホルダ {{name}} {{company_name}}
  {{department}} {{job_title}} で表現する（実名を埋め込まない）。
- 各 EmailBlock に reason_for_inclusion（そのブロックをこのセグメントに含めた理由）を必ず記述する。
- 件名は20〜40文字程度、具体的で開封したくなるもの。
- 本文はビジネス敬語（〜です・ます調）。1ブロック100〜200文字程度。
- associated_asset_ids: 参照したコンテンツ資産の asset_id を設定する。
"""
    client = genai.Client()
    response = client.models.generate_content(
        model=_MODEL,
        contents=f"セグメント「{bucket}」向けのメールパターンを作成してください。",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=_PatternSchema,
        ),
    )
    record_llm_response(space, _MODEL, response)
    pattern = _PatternSchema.model_validate_json(response.text)
    return {
        "bucket": bucket,
        "subject": pattern.subject,
        "blocks": [b.model_dump() for b in pattern.blocks],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Stage 2b: 決定論的な組み立て（LLM不使用） ────────────────────────────────

def _fill(template: str, contact: dict) -> str:
    """テンプレート中のプレースホルダを Contact の値で決定論的に置換する。"""
    values = {f: str(contact.get(f, "") or "") for f in _PLACEHOLDER_FIELDS}
    for key, val in values.items():
        template = template.replace(f"{{{key}}}", val)
    return template


def _assemble_run(db: Any, segment: Segment, patterns: dict[str, dict], run_id: str) -> int:
    """割り当てとパターンから各コンタクトの ComposedEmail を組み立てて保存する。"""
    assignments = [
        a.to_dict()
        for a in db.collection(f"segments/{segment.segment_id}/assignments").get()
    ]
    db.collection("marketing_runs").document(run_id).set({
        "run_id": run_id,
        "status": "running",
        "segment_id": segment.segment_id,
        "purpose": segment.purpose,
        "total": len(assignments),
        "done": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    done = 0
    for asn in assignments:
        contact_id = asn.get("contact_id", "")
        bucket = asn.get("bucket", "")
        pattern = patterns.get(bucket)
        if not pattern:
            logger.warning("no pattern for bucket '%s' (contact %s)", bucket, contact_id)
            continue
        contact = _find_contact(db, contact_id) or {"contact_id": contact_id}

        blocks = [
            EmailBlock(
                block_type=b.get("block_type", ""),
                reason_for_inclusion=b.get("reason_for_inclusion", ""),
                associated_asset_ids=b.get("associated_asset_ids", []),
                block_text=_fill(b.get("block_text", ""), contact),
            )
            for b in pattern.get("blocks", [])
        ]
        email_id = f"email_{uuid.uuid4().hex[:12]}"
        email = ComposedEmail(
            email_id=email_id,
            contact_id=contact_id,
            event_id=contact.get("source_event_id"),
            run_id=run_id,
            subject=_fill(pattern.get("subject", ""), contact),
            blocks=blocks,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.collection(f"marketing_runs/{run_id}/emails").document(email_id).set(email.model_dump())
        done += 1

    db.collection("marketing_runs").document(run_id).update(
        {"status": "done", "done": done, "email_count": done}
    )
    logger.info("assembly completed: run_id=%s segment=%s done=%d", run_id, segment.segment_id, done)
    return done


def _find_contact(db: Any, contact_id: str) -> dict | None:
    """全イベントのバッチからコンタクトを検索する（db はスペース前置済み）。"""
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
