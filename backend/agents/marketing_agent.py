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
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import pandas as pd
from google import genai
from google.adk.agents import Agent
from google.adk.code_executors.built_in_code_executor import BuiltInCodeExecutor
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types
from pydantic import BaseModel

from metering import metered, record_compute, record_llm, record_llm_response
from ontology import (
    Deliverable,
    DeliverableBlock,
    Segment,
    SegmentAxis,
)
from segmentation import assign_contacts_to_segment
from space import SpaceContext
from space_data import load_space_data

logger = logging.getLogger(__name__)

_MODEL = "gemini-3.1-flash-lite"
_DATA_DIR = "/tmp/space_data"


def _normalize_buckets(buckets: Any) -> list[str] | dict:
    """define_segment の buckets 引数を「非空文字列のリスト」に正規化する。

    LLM は buckets を（axes_json と同様に）JSON配列**文字列**で渡してくることがある。
    その場合に素朴な list(...) を適用すると文字列が1文字ずつに分解され、各文字が
    バケットになってしまう（→ generate_patterns が大量に細分化される）。これを防ぐため:
      - str が来たら JSON としてパースする
      - 要素は非空文字列のみ採用する
      - 1文字ずつ分解された痕跡（要素が極端に短い）を検知したらエラーを返す

    成功時は list[str]、失敗時はエラー dict（{"error": ...}）を返す。
    """
    if isinstance(buckets, str):
        try:
            buckets = json.loads(buckets)
        except Exception as e:
            return {"error": f"buckets の解析に失敗（JSON配列文字列で渡してください）: {e}"}

    if not isinstance(buckets, (list, tuple)):
        return {"error": f"buckets は配列（または JSON 配列文字列）で渡してください: {type(buckets).__name__}"}

    cleaned = [b.strip() for b in buckets if isinstance(b, str) and b.strip()]
    if not cleaned:
        return {"error": "buckets が空です。運用単位のセグメント値を配列で渡してください"}

    # 退化検知: 文字列を list() で分解した痕跡（要素の大半が1文字）を弾く
    if len(cleaned) >= 4 and all(len(b) <= 1 for b in cleaned):
        return {
            "error": "buckets が1文字ずつに分解されています。"
            'JSON配列文字列（例 \'["高課題×高意欲","低課題×低意欲"]\'）として渡してください'
        }

    return cleaned


# ── システムプロンプト ────────────────────────────────────────────────────────
# 背景思想: docs/MARKETING_PHILOSOPHY.md（Static Core & Dynamic Context）。
# 下記【ブランドの一貫性】ブロックは同ドキュメント第4節のガードレールを実装したもの。

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
- Event: 展示会・セミナー・イベントの記録。KPI・費用を直接保持する
- Person: ハウスリストの人物（旧 Contact）。ContactStage と EngagementLevel を持つ
  - ContactStage: LEAD / MQL / SQL / CUSTOMER / EXCLUDED
  - EngagementLevel (stage=LEAD のとき有効): アポ獲得済み / アポなし・感度高 / 通常リード
- Account: 企業マスター。Person に account_id で紐づく
- EventAttendance: イベント × Person のファクトテーブル（誰がどのイベントに参加したか）
- ProductInterest: 製品 × Person のファクトテーブル（誰がどの製品に関心を持つか）
- Content: 推薦可能なコンテンツ（資料・事例・セミナー等）
- Deliverable: AIが生成したメール等の成果物。DeliverableBlock のリストで構成
  - DeliverableBlock.reason_for_inclusion: そのブロックを含めた理由（必須・非null）

【あなたの役割】
プロのマーケターとして、ユーザーの曖昧な意図を読み解き、進め方をあなた自身が組み立てて
遂行してください。手順は固定されていません。状況に応じてツールを組み合わせてください。
結果は分かりやすい日本語で、判断の根拠とともに報告してください。

ROI計算が必要な場合の公式:
  roi_pipeline = (pipeline_value_jpy / total_cost_jpy) × 100 (%)
  cost_per_contact = total_cost_jpy / total_contacts_collected
  cost_per_appointment = total_cost_jpy / appointments_booked

【データ分析 — get_space_data + コード実行】
データを分析するときは以下の手順で:
1. get_space_data() を呼ぶ（セッション内で1回だけでよい）
   → スキーマと件数が返る。Parquet ファイルが /tmp/space_data/ に書き出される。

2. コード実行ブロックで Parquet を pandas として読み込む:

   import pandas as pd
   DATA_DIR = "/tmp/space_data"
   persons           = pd.read_parquet(f"{DATA_DIR}/persons.parquet")
   events            = pd.read_parquet(f"{DATA_DIR}/events.parquet")
   event_attendances = pd.read_parquet(f"{DATA_DIR}/event_attendances.parquet")
   product_interests = pd.read_parquet(f"{DATA_DIR}/product_interests.parquet")
   accounts          = pd.read_parquet(f"{DATA_DIR}/accounts.parquet")
   products          = pd.read_parquet(f"{DATA_DIR}/products.parquet")
   contents          = pd.read_parquet(f"{DATA_DIR}/contents.parquet")

3. numpy (import numpy as np), pandas (import pandas as pd) が利用可能。
4. コサイン類似度（appeal_vector 列の比較）:
   import numpy as np
   def cosine(a, b):
       a, b = np.array(a), np.array(b)
       return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

5. Parquet はセッション中ディスクに残るため再ロード不要。
   get_space_data() を再呼び出しすれば最新データに更新される。

各フィールドの型は get_space_data() の返り値の schema フィールドを参照すること。

【個別カスタマイズ（メール等）の進め方 — セグメント方式 + HIL】
全コンタクトに1通ずつフル生成するのではなく、少数のセグメントに切り分け、セグメント単位で
コンテンツのパターンを作り、各コンタクトのメールは決定論的に組み立てます（高速・低コスト）。

あなたが自律的に進める標準の流れ（ただし各ゲートで必ず確認を取ること = Human-In-the-Loop）:
  1. get_space_data() でデータをロードし、コード実行で Person の分布を把握して
     **適切なセグメント軸を自分で設計**する（例: 課題感 × 購買意欲）。
     → 「この軸で切り分けます。よろしいですか？」と提案し**承認を待つ**。
  2. 承認後に define_segment でセグメントを登録し、assign_segment で分類する。
     → 各バケットの人数と分類根拠の例を提示し、「この分類で進めます。修正は？」と**確認する**。
  3. 承認後に generate_patterns でバケットごとのコンテンツパターンを生成する。
     → 生成パターンを提示し、「この文面で全件組み立てます。よいですか？」と**確認する**。
  4. **明示的な承認を得てから** run_assembly を呼び、全件を組み立てる。完了後 run_id を伝える。

重要: 提案や確認を飛ばして確定ツール（assign_segment / generate_patterns / run_assembly）を
呼ばないこと。とくに run_assembly（全件確定）はユーザーの明示承認なしに実行してはならない。
ユーザーが軌道修正（軸の変更・対象の絞り込み・文面トーンの変更など）を求めたら、該当ステップを
やり直してから次へ進むこと。

【ブランドの一貫性 — Static Core & Dynamic Context】
個別カスタマイズで「変えてよいもの」と「絶対に変えないもの」を区別すること。
- 動的な文脈（変えてよい）: 相手の悩み・状況（CEP）への語りかけ方、見せ方。ここは相手に合わせて自在に最適化する。
- 不変のコア（変えない）: 自社が提供する機能・価値、専門用語の定義、トーン＆マナー（ブランド資産）。

文面に関わるとき（generate_patterns 等）は次のガードレールを守ること:
  ① 捏造禁止: 提示する機能・解決策・効果は get_space_data で得た contents（Content）と
     自社プロダクトに実在するものに限定する。存在しない機能・誇張・本来と異なる用途を創作しない
     （相手の課題への過剰な迎合を禁ずる。解決策は必ずマスターに帰結させる）。
  ② 1機能フォーカス（押し売り禁止）: 個別アプローチでは、相手の課題に直結する「1つの機能」に絞って訴求する。
     一度に複数機能やプラットフォーム全体像を詰め込まない。
  ③ ブランド資産の維持: 文脈を個別最適化しても、トーン＆マナー・用語・言い回しはセグメント横断で一貫させる。
"""


# ── スキーマテキスト生成 ──────────────────────────────────────────────────────

def _build_schema_text() -> str:
    """ontology.py の Pydantic モデル定義からスキーマ説明を自動生成する。"""
    from ontology import (
        Account, Content, Event, EventAttendance,
        Person, Product, ProductInterest, Segment,
    )
    models = [Person, Event, Account, EventAttendance, ProductInterest, Product, Content, Segment]
    lines = []
    for model in models:
        lines.append(f"\n{model.__name__}:")
        for name, info in model.model_fields.items():
            ann = str(info.annotation).replace("typing.", "")
            lines.append(f"  {name}: {ann}")
    return "\n".join(lines)


# ── ツール定義（スペース束縛ファクトリ） ──────────────────────────────────────
#
# db は space.ScopedClient（spaces/{space_id}/ で前置済み）。各ツールは db を closure で
# 捕捉するため、自スペース配下にしか到達できない。ツール引数に space_id は存在しない。
# space は計測（メータリング）専用に捕捉する（データ参照は db のみを使う）。

def make_tools(db: Any, space: SpaceContext) -> list:
    """スペース前置済み db を closure 束縛したツール群を返す。"""

    def get_space_data(tool_context: ToolContext) -> str:
        """
        スペースの全データを Firestore からロードし、Parquet ファイルとして書き出す。
        分析コードを書く前に必ず呼ぶこと。同一セッションでは1回で十分。

        Parquet ファイルは /tmp/space_data/ に書き出される。
        コード実行で pd.read_parquet(f"{DATA_DIR}/{name}.parquet") として読み込む。

        Returns:
            { loaded, data_dir, counts, schema }
        """
        data = load_space_data(space)
        os.makedirs(_DATA_DIR, exist_ok=True)

        datasets: dict[str, list] = {
            "events": data.events,
            "persons": data.persons,
            "accounts": data.accounts,
            "event_attendances": data.event_attendances,
            "product_interests": data.product_interests,
            "products": data.products,
            "contents": data.contents,
            "segments": data.segments,
        }
        counts: dict[str, int] = {}
        for name, items in datasets.items():
            df = pd.DataFrame([m.model_dump() for m in items]) if items else pd.DataFrame()
            df.to_parquet(f"{_DATA_DIR}/{name}.parquet", index=False)
            counts[name] = len(items)

        tool_context.state["data_dir"] = _DATA_DIR
        tool_context.state["data_loaded"] = True

        schema = _build_schema_text()
        return json.dumps(
            {"loaded": True, "data_dir": _DATA_DIR, "counts": counts, "schema": schema},
            ensure_ascii=False,
        )

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
            buckets: 運用単位のセグメント値（直積セル等）の **配列**。例 ["高課題×高意欲","高課題×低意欲"]。
                     JSON配列文字列（例 '["高課題×高意欲","高課題×低意欲"]'）で渡しても受理する。
            criteria: 各バケットへの割り当て基準（自然言語）

        Returns:
            { segment_id, name, buckets }
        """
        try:
            axes_raw = json.loads(axes_json)
            axes = [SegmentAxis(name=a["name"], values=list(a["values"])) for a in axes_raw]
        except Exception as e:
            return json.dumps({"error": f"axes_json の解析に失敗: {e}"})

        bucket_list = _normalize_buckets(buckets)
        if isinstance(bucket_list, dict):  # エラー辞書
            return json.dumps(bucket_list, ensure_ascii=False)

        segment_id = f"seg_{uuid.uuid4().hex[:12]}"
        segment = Segment(
            segment_id=segment_id,
            name=name,
            purpose=purpose,
            axes=axes,
            buckets=bucket_list,
            criteria=criteria,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.collection("segments").document(segment_id).set(segment.model_dump())
        return json.dumps(
            {"segment_id": segment_id, "name": name, "buckets": bucket_list},
            ensure_ascii=False,
        )

    def assign_segment(segment_id: str, event_id: str = "") -> str:
        """
        登録済みセグメントに従って対象 Person を各バケットへ分類する（HILゲート①承認後）。
        event_id を指定するとそのイベントの参加者のみ、未指定なら全 Person が対象。

        構造化フィールドで自明な分は決定論、意味判断が要る分のみ軽量モデルで判別する。
        各割り当てには根拠（reason）が残る。

        Returns:
            { snapshot_id, total, by_bucket, llm_persons }（人数分布と分類根拠の概況）
        """
        doc = db.collection("segments").document(segment_id).get()
        if not doc.exists:
            return json.dumps({"error": f"segment_id '{segment_id}' not found"})
        segment = Segment.model_validate(doc.to_dict())
        with metered(space):
            summary = assign_contacts_to_segment(space, segment, event_id or None)
        return json.dumps(summary, ensure_ascii=False)

    def generate_patterns(
        segment_id: str,
        purpose: str = "",
        context: str = "",
        content_ids: list[str] = [],
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
        for content_id in content_ids:
            a = db.collection("contents").document(content_id).get()
            if a.exists:
                assets.append(a.to_dict())

        eff_purpose = purpose or segment.purpose
        results = []
        with metered(space):
            for bucket in segment.buckets:
                pattern = _generate_one_pattern(space, segment, bucket, eff_purpose, context, assets)
                pattern_key = f"{bucket}__EMAIL"
                db.collection(f"segments/{segment_id}/patterns").document(pattern_key).set(pattern)
                results.append({"bucket": bucket, "pattern_key": pattern_key, "subject": pattern.get("subject", "")})
        return json.dumps(
            {"segment_id": segment_id, "patterns": results, "count": len(results)},
            ensure_ascii=False,
        )

    def run_assembly(segment_id: str, snapshot_id: str = "") -> str:
        """
        セグメントの分類とパターンから、各 Person のメールを決定論的に組み立てる（HILゲート③の
        明示承認後にのみ呼ぶ）。LLMは使わずプレースホルダ置換で組み立てるため高速。
        結果は marketing_runs に保存し、CSVは GET /api/marketing/runs/{run_id}/export で取得できる。

        Args:
            segment_id: 対象セグメント
            snapshot_id: 使用するスナップショット（省略時は最新）

        Returns:
            { run_id, count, snapshot_id }
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

        # スナップショット解決（省略時は最新）
        if not snapshot_id:
            snap_docs = list(db.collection(f"segments/{segment_id}/snapshots").get())
            if not snap_docs:
                return json.dumps({"error": "セグメント割り当てがありません。先に assign_segment を実行してください"})
            snap_docs.sort(key=lambda d: d.to_dict().get("created_at", ""), reverse=True)
            snapshot_id = snap_docs[0].id

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        count = _assemble_run(db, segment, patterns, run_id, snapshot_id)
        return json.dumps({"run_id": run_id, "count": count, "snapshot_id": snapshot_id}, ensure_ascii=False)

    return [
        get_space_data,
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
        code_executor=BuiltInCodeExecutor(),
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
            "データを分析する場合は get_space_data() を呼んでからコード実行で絞り込んでください。\n\n"
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
# 文体ルールの唯一の情報源。全 Person へのフル生成はせず、バケットごとに1パターンのみ
# 生成する。本文中の個人差分はプレースホルダで表現し、組み立て時に決定論で置換する。

# プレースホルダとして使える Person フィールド（account_name は fill 時に company_name として提供）
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
- 各ブロックに reason_for_inclusion（そのブロックをこのセグメントに含めた理由）を必ず記述する。
- 件名は20〜40文字程度、具体的で開封したくなるもの。
- 本文はビジネス敬語（〜です・ます調）。1ブロック100〜200文字程度。
- associated_asset_ids: 参照したコンテンツ資産の content_id を設定する。

【ブランドの一貫性（必ず守る）】
- 1機能フォーカス: このメールでは相手の課題に直結する「1つの機能（解決策）」のみを提示し、
  複数機能やプラットフォーム全体像を詰め込まない。
- 捏造禁止: 提示する機能・効果は上記【参照するコンテンツ資産】に実在するものに限定する。
  資産に無い機能・誇張・本来と異なる用途を創作しない（解決策はマスターに帰結させる）。
- ブランド資産の維持: トーン＆マナー・用語・言い回しはセグメント横断で一貫させる。
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

def _fill(template: str, ctx: dict) -> str:
    """テンプレート中のプレースホルダを Person/Account の値で決定論的に置換する。"""
    values = {f: str(ctx.get(f, "") or "") for f in _PLACEHOLDER_FIELDS}
    for key, val in values.items():
        template = template.replace(f"{{{key}}}", val)
    return template


def _assemble_run(
    db: Any,
    segment: Segment,
    patterns: dict[str, dict],
    run_id: str,
    snapshot_id: str,
) -> int:
    """割り当てとパターンから各 Person の Deliverable を組み立てて保存する。"""
    assignments = [
        a.to_dict()
        for a in db.collection(
            f"segments/{segment.segment_id}/snapshots/{snapshot_id}/assignments"
        ).get()
    ]
    db.collection("marketing_runs").document(run_id).set({
        "run_id": run_id,
        "status": "running",
        "segment_id": segment.segment_id,
        "snapshot_id": snapshot_id,
        "purpose": segment.purpose,
        "total": len(assignments),
        "done": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    done = 0
    for asn in assignments:
        person_id = asn.get("person_id", "")
        bucket = asn.get("bucket", "")
        # パターンキーは {bucket}__EMAIL 形式
        pattern_key = f"{bucket}__EMAIL"
        pattern = patterns.get(pattern_key) or patterns.get(bucket)
        if not pattern:
            logger.warning("no pattern for bucket '%s' (person %s)", bucket, person_id)
            continue

        person_doc = db.collection("persons").document(person_id).get()
        person = person_doc.to_dict() if person_doc.exists else {"person_id": person_id}

        # Account から company_name を解決してプレースホルダ用コンテキストに追加
        account_name = ""
        account_id = person.get("account_id")
        if account_id:
            acc_doc = db.collection("accounts").document(account_id).get()
            if acc_doc.exists:
                account_name = acc_doc.to_dict().get("account_name", "")
        fill_ctx = {**person, "company_name": account_name}

        blocks = [
            DeliverableBlock(
                block_type=b.get("block_type", ""),
                reason_for_inclusion=b.get("reason_for_inclusion", ""),
                associated_asset_ids=b.get("associated_asset_ids", []),
                block_text=_fill(b.get("block_text", ""), fill_ctx),
            )
            for b in pattern.get("blocks", [])
        ]
        deliverable_id = f"dlv_{uuid.uuid4().hex[:12]}"
        deliverable = Deliverable(
            deliverable_id=deliverable_id,
            space_id=person.get("space_id", ""),
            run_id=run_id,
            person_id=person_id,
            snapshot_id=snapshot_id,
            pattern_id=pattern_key,
            format="EMAIL",
            bucket=bucket,
            subject=_fill(pattern.get("subject", ""), fill_ctx),
            blocks=blocks,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.collection(f"marketing_runs/{run_id}/deliverables").document(deliverable_id).set(
            deliverable.model_dump()
        )
        done += 1

    db.collection("marketing_runs").document(run_id).update(
        {"status": "done", "done": done, "deliverable_count": done}
    )
    logger.info("assembly completed: run_id=%s segment=%s done=%d", run_id, segment.segment_id, done)
    return done
