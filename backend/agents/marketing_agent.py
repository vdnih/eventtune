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
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import vertexai
from google import genai
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import VertexAiSessionService
from google.adk.tools import ToolContext
from google.genai import types
from pydantic import BaseModel
from vertexai import types as vai_types

from config import get_settings
from metering import metered, record_compute, record_llm, record_llm_response
from ontology import (
    Deliverable,
    DeliverableBlock,
    DeliverablePattern,
    MarketingRun,
    Segment,
    SegmentAxis,
)
from segmentation import assign_contacts_to_segment
from semantic_search import find_similar
from space import SpaceContext
from space_data import load_space_data

logger = logging.getLogger(__name__)


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
        return {
            "error": f"buckets は配列（または JSON 配列文字列）で渡してください: {type(buckets).__name__}"
        }

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
あなたは EventTune（イベントマーケティング・インテリジェンス）のマーケティングエージェントです。

【プラットフォームの思想】
このプラットフォームは、展示会・セミナー・イベントを中心に、カオスなマーケティングデータを
オントロジー（OSI セマンティックレイヤー）に統合し、AIエージェントがマーケティング活動を
行うための基盤です。データは星座型（ファクト・コンステレーション）で、複数のマスタ（持続する
実体）を、参加・関心といったファクト（出来事）が結びつける構造になっています。

設計原則:
- Ontology-First: データは必ずオントロジーへのマッピングを経由する
- Semantic Affinity: 「誰に何が合うか」は固定の課題ラベルではなく、各実体の appeal_summary
  （関心・価値の自然文要約）と appeal_vector（その埋め込み）の意味的近接（コサイン類似度）で表す
- Auditable AI: すべてのAI判断には根拠が必要。reason_for_inclusion は Optional 不可
- Historical Intelligence: イベントをまたいだ蓄積・比較・学習が価値を持つ

【オントロジーの定義】
マスタ（持続する実体。それぞれ appeal_summary / appeal_vector を持つ）:
- Person: ハウスリストの人物（旧 Contact）。ContactStage と EngagementLevel を持つ
  - ContactStage: LEAD / MQL / SQL / CUSTOMER / EXCLUDED
  - EngagementLevel (stage=LEAD のとき有効): アポ獲得済み / アポなし・感度高 / 通常リード
- Account: 企業マスター。Person に account_id で紐づく
- Product: 製品マスター
- Content: 推薦可能なコンテンツ（資料・事例・セミナー等）
- Event: 展示会・セミナー・イベントの記録。KPI・費用を直接保持する
ファクト（マスタ同士を結ぶ出来事テーブル）:
- EventAttendance: イベント × Person（誰がどのイベントに参加したか）
- ProductInterest: 製品 × Person（誰がどの製品に関心を持つか）
成果物:
- Deliverable: AIが生成したメール等の成果物。format（EMAIL/TALK_SCRIPT/PROPOSAL）と
  DeliverableBlock のリストで構成
  - DeliverableBlock.reason_for_inclusion: そのブロックを含めた理由（必須・非null）

【意味検索 — find_relevant_for_person】
「この人に合うコンテンツ/製品/イベント」を引くときは find_relevant_for_person(person_id, target)
を使う（target = "contents" | "products" | "events"）。appeal_vector のコサイン近接で上位候補と
その appeal_summary・類似スコアが返る。これを個別メールのコンテンツ選定やおすすめの根拠に使う。
固定の課題ラベルで決め打ちせず、必ず意味的近接（と appeal_summary）に帰結させること。

【あなたの役割】
プロのマーケターとして、ユーザーの曖昧な意図を読み解き、進め方をあなた自身が組み立てて
遂行してください。手順は固定されていません。状況に応じてツールを組み合わせてください。
結果は分かりやすい日本語で、判断の根拠とともに報告してください。
手元のデータで価値を出すことを最優先にし、欲しい数値が無いことを理由に分析を放棄しないこと。

ROI は KPI フィールド（pipeline_value_jpy / total_contacts_collected / appointments_booked / total_budget）が
**入力済みのときに限り**算出する（無ければ算出せず、未入力である旨を注記するだけにとどめる）。公式:
  roi_pipeline = (pipeline_value_jpy / total_cost_jpy) × 100 (%)
  cost_per_contact = total_cost_jpy / total_contacts_collected
  cost_per_appointment = total_cost_jpy / appointments_booked

【データ分析 — get_space_data + run_python_code】
分析は必ず「run_python_code ツールで Python を実行し、その出力（実データの計算結果）に基づいて」行うこと。
数値を推測・暗算で答えてはならない。手順:

1. get_space_data() を呼ぶ（セッション内で1回だけでよい）
   → スキーマ・件数と、サンドボックスに配置された CSV ファイル名一覧が返る。

2. run_python_code(code) ツールを呼んで分析する。CSV は作業ディレクトリにあるので pd.read_csv で読む。例:

   run_python_code(code="persons = pd.read_csv('persons.csv'); print(persons.columns.tolist())")

   他に events.csv / event_attendances.csv / product_interests.csv / accounts.csv / products.csv /
   contents.csv がある。get_space_data 実行後は pandas が pd、numpy が np として import 済みで使える。
   scipy 等それ以外を使うときは自分で import する（pip install は不可）。

【run_python_code の作法（重要）】
- コードの実行は必ず run_python_code ツールで行う。run_python_code 以外に「コードを実行するツール」は無い。
- ステートフル: 変数・import・読み込んだファイルはセッション内で持続する。前回 run_python_code で定義した
  変数や import は次の呼び出しでもそのまま使える。再初期化・再読込・再 import は不要。
- 出力は必ず print() する（標準出力に出した内容だけが結果として返る）。
  例: print(df.shape) / print(f"{roi=}") のように、確認したい値を明示的に出力する。
- データの中身を勝手に仮定しない。列名・型・欠損は df.head() や df.columns で実際に確認してから使う。
- 各フィールドの型は get_space_data() の返り値の schema を参照する。

【分析・振り返りの進め方（重要）— 1テーブルで早合点しない】
1. まず俯瞰する: 1つの CSV だけ見て結論を出さない。関係する全データセットの件数・主要列・非欠損状況を
   確認してから分析に入る。
2. ファクトテーブルを横断する（Firestore は JOIN しないので pandas で結合・集計する）。具体例:
   - イベントの**実参加者数** = event_attendances を event_id で件数集計
     （例: event_attendances[event_attendances.event_id == X].shape[0]）。events の KPI 列ではなくここから出す。
   - 参加者の属性分布 = event_attendances → persons（→ accounts）を person_id/account_id で merge し、
     contact_stage / engagement_level / 業種・企業規模で集計。
   - 関心製品の分布 = product_interests を product_id・イベント別に集計。
   - 予算・目標名刺数・定性メモ = events の total_budget / target_contact_count / description。
3. **欠損は「止まる理由」ではなく「注記」**: ある数値列（KPI 等）が NaN でも分析を放棄しない。
   まず出せる要約を必ず提示し、そのうえで「○○は未入力のため算出不可。入力すれば算出可能」と添える。
   いきなり「データが無いので振り返れません／入力してください／定性ヒアリングしましょう」に切り替えない。
4. 「イベントの振り返り」の既定アウトプット例:
   概要（名称/種別/会場/会期/予算/目標名刺数）＋ 実参加者数（attendances）＋ 参加者の属性・関心の分布
   ＋ description の定性要点 ＋（KPI があれば）ROI、無ければ未入力項目の注記。

【個別カスタマイズ（メール等）の進め方 — セグメント方式 + HIL】
全コンタクトに1通ずつフル生成するのではなく、少数のセグメントに切り分け、セグメント単位で
コンテンツのパターンを作り、各コンタクトのメールは決定論的に組み立てます（高速・低コスト）。

あなたが自律的に進める標準の流れ（ただし各ゲートで必ず確認を取ること = Human-In-the-Loop）:
  1. get_space_data() でデータをロードし、run_python_code で Person の分布を把握して
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
        Account,
        Content,
        Event,
        EventAttendance,
        Person,
        Product,
        ProductInterest,
        Segment,
    )

    models = [Person, Event, Account, EventAttendance, ProductInterest, Product, Content, Segment]
    lines = []
    for model in models:
        lines.append(f"\n{model.__name__}:")
        for name, info in model.model_fields.items():
            ann = str(info.annotation).replace("typing.", "")
            lines.append(f"  {name}: {ann}")
    return "\n".join(lines)


# ── Agent Engine コード実行サンドボックス ─────────────────────────────────────
#
# コード実行は ADK の code_executor（CodeAct）でなく run_python_code 関数ツールで行う。
# サンドボックスは Agent Engine 上に「セッション毎に1つ」作り、tool_context.state に名前を保持して
# 再利用する（変数・ファイルが持続するステートフル実行）。詳細は docs/ADR.md ADR-009。

_vertex: vertexai.Client | None = None


def _vertex_client() -> vertexai.Client:
    global _vertex
    if _vertex is None:
        settings = get_settings()
        _vertex = vertexai.Client(
            project=settings.google_cloud_project,
            location=settings.agent_runtime_location,
        )
    return _vertex


def _ensure_sandbox(tool_context: ToolContext) -> str:
    """セッション用のコード実行サンドボックスを確保し、resource name を返す。

    既存（RUNNING）があれば再利用、無ければ Agent Engine 上に新規作成して state に保存する。
    """
    from google.api_core import exceptions as gapi_exc
    from google.genai import errors as genai_errors

    client = _vertex_client()
    name = tool_context.state.get("sandbox_name")
    if name:
        try:
            sb = client.agent_engines.sandboxes.get(name=name)
            if sb is not None and getattr(sb, "state", None) == "STATE_RUNNING":
                return name
        except (gapi_exc.NotFound, genai_errors.ClientError):
            pass  # 失効 → 作り直す

    settings = get_settings()
    op = client.agent_engines.sandboxes.create(
        spec={"code_execution_environment": {}},
        name=settings.agent_engine_resource_name,
        config=vai_types.CreateAgentEngineSandboxConfig(
            display_name="marketing_agent_sandbox",
            ttl="3600s",
        ),
    )
    name = op.response.name
    tool_context.state["sandbox_name"] = name
    return name


def _exec_in_sandbox(
    sandbox_name: str, code: str, files: list[dict] | None = None
) -> tuple[str, str]:
    """サンドボックスでコードを実行し (stdout, stderr) を返す。

    files は [{"name","content"(bytes),"mimeType"}]。execute_code が読むのは 'content'（単数）＋生 bytes。
    """
    input_data: dict[str, Any] = {"code": code}
    if files:
        input_data["files"] = files
    resp = _vertex_client().agent_engines.sandboxes.execute_code(
        name=sandbox_name, input_data=input_data
    )
    stdout, stderr = "", ""
    for out in resp.outputs:
        attrs = getattr(getattr(out, "metadata", None), "attributes", None)
        if out.mime_type == "application/json" and (attrs is None or "file_name" not in attrs):
            j = json.loads(out.data.decode("utf-8"))
            stdout, stderr = j.get("msg_out", ""), j.get("msg_err", "")
    return stdout, stderr


# ── ツール定義（スペース束縛ファクトリ） ──────────────────────────────────────
#
# db は space.ScopedClient（spaces/{space_id}/ で前置済み）。各ツールは db を closure で
# 捕捉するため、自スペース配下にしか到達できない。ツール引数に space_id は存在しない。
# space は計測（メータリング）専用に捕捉する（データ参照は db のみを使う）。


def make_tools(db: Any, space: SpaceContext) -> list:
    """スペース前置済み db を closure 束縛したツール群を返す。"""

    def get_space_data(tool_context: ToolContext) -> str:
        """
        スペースの全データを Firestore からロードし、コード実行サンドボックスへ CSV として投入する。
        分析（run_python_code）の前に必ず1回呼ぶこと。

        各データセットは "{name}.csv"（persons.csv 等）としてサンドボックスの作業ディレクトリに置かれ、
        以降の run_python_code から pd.read_csv("persons.csv") で読める（ファイルはセッション内で持続）。

        Returns:
            { loaded, files, counts, schema }
        """
        data = load_space_data(space)

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
        files: list[dict] = []
        for name, items in datasets.items():
            counts[name] = len(items)
            df = pd.DataFrame([m.model_dump() for m in items]) if items else pd.DataFrame()
            # appeal_vector（埋め込み）は CSV で文字列化して扱いづらく、分析には不要なので落とす。
            # 類似度計算は semantic_search.py（決定論 Python）が担う。
            if "appeal_vector" in df.columns:
                df = df.drop(columns=["appeal_vector"])
            files.append(
                {
                    "name": f"{name}.csv",
                    "content": df.to_csv(index=False).encode("utf-8"),
                    "mimeType": "text/csv",
                }
            )

        sandbox = _ensure_sandbox(tool_context)
        # ファイルをサンドボックスへ投入し、pandas/numpy を pre-import する。
        # サンドボックスはステートフルなので、ここでの import とファイルは後続 run_python_code でも残る
        # （= モデルは pd / np を import なしで使える）。
        setup = (
            "import pandas as pd\n"
            "import numpy as np\n"
            "import os\n"
            "print(sorted(p for p in os.listdir('.') if p.endswith('.csv')))"
        )
        _, stderr = _exec_in_sandbox(sandbox, setup, files=files)
        if stderr:
            logger.warning("get_space_data upload stderr: %s", stderr)
        tool_context.state["data_loaded"] = True

        schema = _build_schema_text()
        return json.dumps(
            {
                "loaded": True,
                "files": [f["name"] for f in files],
                "counts": counts,
                "schema": schema,
            },
            ensure_ascii=False,
        )

    def run_python_code(code: str, tool_context: ToolContext) -> str:
        """
        Python コードをコード実行サンドボックスで実行し、標準出力を返す。データ分析はこのツールで行う。

        - 事前に get_space_data() を呼ぶこと（persons.csv 等が作業ディレクトリに配置される）。
        - pandas/numpy/scipy/matplotlib は import 済みで利用可能（pip install は不可）。
        - 結果は必ず print() すること（標準出力だけが返る）。
        - ステートフル: 変数・import・ファイルはセッション内で持続する（再読込・再定義は不要）。

        Args:
            code: 実行する Python コード。

        Returns:
            { stdout, stderr } の JSON 文字列。
        """
        from google.api_core import exceptions as gapi_exc
        from google.genai import errors as genai_errors

        sandbox = tool_context.state.get("sandbox_name")
        if not sandbox:
            return json.dumps(
                {"error": "サンドボックス未初期化です。先に get_space_data() を呼んでください。"},
                ensure_ascii=False,
            )
        try:
            stdout, stderr = _exec_in_sandbox(sandbox, code)
        except (gapi_exc.NotFound, genai_errors.ClientError):
            tool_context.state.pop("sandbox_name", None)
            return json.dumps(
                {"error": "サンドボックスが失効しました。get_space_data() を再実行してください。"},
                ensure_ascii=False,
            )
        return json.dumps({"stdout": stdout, "stderr": stderr}, ensure_ascii=False)

    def find_relevant_for_person(person_id: str, target: str = "contents", top_k: int = 5) -> str:
        """
        指定 Person の appeal_vector に意味的に近い候補を上位 top_k 返す（コサイン類似度・決定論）。

        固定の課題ラベルではなく、人物の関心・文脈の埋め込み（appeal_vector）と各マスタの
        appeal_vector の近接で「この人に合うもの」を引く。個別メールのコンテンツ選定や、
        おすすめ製品・次に案内すべきイベントの根拠出しに使う。

        Args:
            person_id: 対象 Person の ID
            target: "contents" | "products" | "events" のいずれか（既定 contents）
            top_k: 返す件数（既定 5）

        Returns:
            { person_id, target, results: [{id, name, appeal_summary, score}] } の JSON 文字列
        """
        cols = {
            "contents": ("content_id", "content_name"),
            "products": ("product_id", "product_name"),
            "events": ("event_id", "name"),
        }
        if target not in cols:
            return json.dumps({"error": f"target は {list(cols)} のいずれか"}, ensure_ascii=False)

        pdoc = db.collection("persons").document(person_id).get()
        if not pdoc.exists:
            return json.dumps({"error": f"person_id '{person_id}' not found"}, ensure_ascii=False)
        pvec = (pdoc.to_dict() or {}).get("appeal_vector") or []
        if not pvec:
            return json.dumps(
                {
                    "error": "この Person には appeal_vector が無く意味検索できません（取り込み時に未生成）"
                },
                ensure_ascii=False,
            )

        id_field, name_field = cols[target]
        candidates = [
            (d.to_dict(), (d.to_dict() or {}).get("appeal_vector") or [])
            for d in db.collection(target).get()
        ]
        ranked = find_similar(pvec, candidates, top_k=top_k)
        results = [
            {
                "id": item.get(id_field, ""),
                "name": item.get(name_field, ""),
                "appeal_summary": item.get("appeal_summary", ""),
                "score": round(score, 4),
            }
            for item, score in ranked
        ]
        return json.dumps(
            {"person_id": person_id, "target": target, "results": results},
            ensure_ascii=False,
        )

    def save_report(event_id: str, report_type: str, content: str) -> str:
        """
        分析レポートや戦略提案を Firestore に保存する。
        report_type: "retrospective" / "strategy" / その他の自由な文字列
        content: JSON 文字列または自由テキスト
        """
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        db.collection(f"events/{event_id}/reports").document(report_id).set(
            {
                "report_id": report_id,
                "event_id": event_id,
                "report_type": report_type,
                "content": content,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
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
            created_at=datetime.now(UTC).isoformat(),
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
        content_ids: list[str] = [],  # noqa: B006 — ADKツールスキーマ維持のため（読み取り専用）
        output_format: str = "EMAIL",
    ) -> str:
        """
        バケットごとに1つずつコンテンツパターン（件名＋本文ブロック）を生成する（HILゲート②承認後）。
        コスト = バケット数ぶんのLLM呼び出しのみ。生成後はユーザーにレビューさせること。

        本文中の個人差分は {name} {company_name} {department} {job_title} のプレースホルダで表現する。

        Args:
            output_format: 成果物の形式。"EMAIL"（既定）/ "TALK_SCRIPT" / "PROPOSAL"。
                           パターンは "{bucket}__{output_format}" をキーに保存し、同じ format を
                           指定した run_assembly が参照する。

        Returns:
            { segment_id, format, patterns: [{bucket, pattern_id, subject}], count }
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
                pattern = _generate_one_pattern(
                    space, segment, bucket, eff_purpose, context, assets, output_format
                )
                db.collection(f"segments/{segment_id}/patterns").document(pattern.pattern_id).set(
                    pattern.model_dump()
                )
                results.append(
                    {
                        "bucket": bucket,
                        "pattern_id": pattern.pattern_id,
                        "subject": pattern.subject,
                    }
                )
        return json.dumps(
            {
                "segment_id": segment_id,
                "format": output_format,
                "patterns": results,
                "count": len(results),
            },
            ensure_ascii=False,
        )

    def run_assembly(segment_id: str, snapshot_id: str = "", output_format: str = "EMAIL") -> str:
        """
        セグメントの分類とパターンから、各 Person の成果物を決定論的に組み立てる（HILゲート③の
        明示承認後にのみ呼ぶ）。LLMは使わずプレースホルダ置換で組み立てるため高速。
        結果は marketing_runs に保存し、CSVは GET /api/marketing/runs/{run_id}/export で取得できる。

        Args:
            segment_id: 対象セグメント
            snapshot_id: 使用するスナップショット（省略時は最新）
            output_format: 組み立てる形式。generate_patterns で生成した format と一致させること
                           （既定 "EMAIL"）。パターンは "{bucket}__{output_format}" で引く。

        Returns:
            { run_id, count, snapshot_id, format }
        """
        doc = db.collection("segments").document(segment_id).get()
        if not doc.exists:
            return json.dumps({"error": f"segment_id '{segment_id}' not found"})
        segment = Segment.model_validate(doc.to_dict())

        # パターン未生成チェック（pattern_id = "{bucket}__{format}" をキーに保持）
        patterns = {
            p.id: p.to_dict() for p in db.collection(f"segments/{segment_id}/patterns").get()
        }
        if not patterns:
            return json.dumps(
                {"error": "パターンが未生成です。先に generate_patterns を実行してください"}
            )

        # スナップショット解決（省略時は最新）
        if not snapshot_id:
            snap_docs = list(db.collection(f"segments/{segment_id}/snapshots").get())
            if not snap_docs:
                return json.dumps(
                    {
                        "error": "セグメント割り当てがありません。先に assign_segment を実行してください"
                    }
                )
            snap_docs.sort(key=lambda d: d.to_dict().get("created_at", ""), reverse=True)
            snapshot_id = snap_docs[0].id

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        count = _assemble_run(db, segment, patterns, run_id, snapshot_id, output_format)
        return json.dumps(
            {"run_id": run_id, "count": count, "snapshot_id": snapshot_id, "format": output_format},
            ensure_ascii=False,
        )

    return [
        get_space_data,
        run_python_code,
        find_relevant_for_person,
        save_report,
        define_segment,
        assign_segment,
        generate_patterns,
        run_assembly,
    ]


# ── エージェント・ランナー構築 ────────────────────────────────────────────────


def build_agent(db: Any, space: SpaceContext) -> Agent:
    """スペース束縛ツールを持つ Agent をリクエストごとに構築する。

    コード実行は ADK の code_executor ではなく run_python_code 関数ツールで行う（ADR-009）。
    関数ツールはモデルが自然に呼べ、Agent Engine サンドボックス（隔離・ステートフル）を直接叩く。
    """
    return Agent(
        name="marketing_agent",
        model=get_settings().model_agent,
        description="EventTune のマーケティングエージェント。メール生成・振り返り分析・戦略立案を汎用的に担う。",
        instruction=_SYSTEM_PROMPT,
        tools=make_tools(db, space),
    )


# セッションは Agent Engine のマネージドセッションに保存する。Cloud Run のオートスケール/再起動を
# 跨いで session.state（= サンドボックス名）が永続し、ステートフルなコード実行が本番でも機能する。
# app_name は agent_engine_id に解決される（VertexAiSessionService._get_reasoning_engine_id）。
_settings = get_settings()
_session_service = VertexAiSessionService(
    project=_settings.google_cloud_project,
    location=_settings.agent_runtime_location,
    agent_engine_id=_settings.agent_engine_id,
)
_APP_NAME = _settings.agent_engine_id


def _accumulate_usage(event: Any, totals: dict[str, int]) -> None:
    """ADK イベントから usage_metadata を拾って累積する（防御的）。"""
    usage = getattr(event, "usage_metadata", None)
    if usage is None:
        return
    totals["input"] += getattr(usage, "prompt_token_count", 0) or 0
    totals["output"] += getattr(usage, "candidates_token_count", 0) or 0


def _parse_tool_response(resp: Any) -> dict:
    """関数ツールの戻り値（ADK は文字列を {"result": ...} で包むことがある）を dict に正規化する。"""
    inner = resp.get("result", resp) if isinstance(resp, dict) else resp
    if isinstance(inner, str):
        try:
            return json.loads(inner)
        except Exception:
            return {"stdout": inner}
    return inner if isinstance(inner, dict) else {"stdout": str(inner)}


async def ensure_session(session_id: str | None, space: SpaceContext) -> str:
    """Agent Engine セッションを用意し、確定した session_id（= thread_id）を返す。

    Agent Engine の session_id はサーバ採番のため独自IDは渡せない。
    - 既存IDあり → resume（get_session で存在確認、無ければ新規採番）
    - ID未指定   → 新規セッションを採番
    """
    # ADKセッションキーをスペースで名前空間化し、スペース間のセッション混線を防ぐ
    session_user_id = f"{space.space_id}:{space.uid}"
    if session_id:
        session = await _session_service.get_session(
            app_name=_APP_NAME, user_id=session_user_id, session_id=session_id
        )
        if session is not None:
            return session.id
    session = await _session_service.create_session(
        app_name=_APP_NAME,
        user_id=session_user_id,  # session_id 未指定 → サーバ採番
    )
    return session.id


async def chat_stream(
    message: str,
    session_id: str,
    space: SpaceContext,
    event_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    MarketingAgent とのチャットを SSE 用のイベント辞書としてストリーミングする。

    Yields:
        { type: "tool_call" | "tool_result" | "code" | "code_result" | "text" | "done" | "error", ... }
        - code:        AIが生成して実行した Python コード（{code}）
        - code_result: その実行結果（{outcome, output}）
    """
    db = space.scoped_db()
    # ADKセッションキーをスペースで名前空間化し、スペース間のセッション混線を防ぐ。
    # セッション自体は呼び出し元が ensure_session() で用意済み（session_id は確定済みのサーバ採番ID）。
    session_user_id = f"{space.space_id}:{space.uid}"

    runner = Runner(
        agent=build_agent(db, space),
        app_name=_APP_NAME,
        session_service=_session_service,
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
            "データを分析する場合は get_space_data() を呼んでから run_python_code で絞り込んでください。\n\n"
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
            # ToolCall イベント。run_python_code は「AIが実行したコード」として可視化する。
            if event.get_function_calls():
                for fc in event.get_function_calls():
                    if fc.name == "run_python_code":
                        yield {"type": "code", "code": (fc.args or {}).get("code", "")}
                    else:
                        yield {
                            "type": "tool_call",
                            "tool_name": fc.name,
                            "args": dict(fc.args) if fc.args else {},
                        }
            # ToolResponse イベント。run_python_code の結果はコード実行結果として可視化する。
            elif event.get_function_responses():
                for fr in event.get_function_responses():
                    if fr.name == "run_python_code":
                        parsed = _parse_tool_response(fr.response)
                        out = parsed.get("stdout") or parsed.get("error") or ""
                        if parsed.get("stderr"):
                            out = f"{out}\n{parsed['stderr']}" if out else parsed["stderr"]
                        yield {
                            "type": "code_result",
                            "outcome": "ERROR"
                            if (parsed.get("stderr") or parsed.get("error"))
                            else "OK",
                            "output": out,
                        }
                    else:
                        yield {
                            "type": "tool_result",
                            "tool_name": fr.name,
                            "result": fr.response
                            if isinstance(fr.response, dict)
                            else {"value": fr.response},
                        }
            # 最終テキスト応答
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
        record_llm(space, get_settings().model_agent, usage_totals["input"], usage_totals["output"])
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
    block_text: str  # {name} {company_name} {department} {job_title} を含めてよい


class _PatternSchema(BaseModel):
    subject: str  # プレースホルダ可
    blocks: list[_PatternBlock]


def _generate_one_pattern(
    space: SpaceContext,
    segment: Segment,
    bucket: str,
    purpose: str,
    context: str,
    assets: list[dict],
    output_format: str = "EMAIL",
) -> DeliverablePattern:
    """1バケットぶんのコンテンツパターンを生成して DeliverablePattern で返す。"""
    assets_text = json.dumps(assets, ensure_ascii=False, indent=2) if assets else "（なし）"
    format_label = {
        "EMAIL": "メール",
        "TALK_SCRIPT": "電話・商談トークスクリプト",
        "PROPOSAL": "提案書",
    }.get(output_format, "メール")
    system_prompt = f"""\
あなたはプロのマーケターです。施策「{segment.name}」のために、あるセグメントへ届ける
{format_label}の**ひな型（パターン）**を1つ作成してください。個々人に1通ずつではなく、この
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
- 1機能フォーカス: この{format_label}では相手の課題に直結する「1つの機能（解決策）」のみを提示し、
  複数機能やプラットフォーム全体像を詰め込まない。
- 捏造禁止: 提示する機能・効果は上記【参照するコンテンツ資産】に実在するものに限定する。
  資産に無い機能・誇張・本来と異なる用途を創作しない（解決策はマスターに帰結させる）。
- ブランド資産の維持: トーン＆マナー・用語・言い回しはセグメント横断で一貫させる。
"""
    _model = get_settings().model_content
    client = genai.Client()
    response = client.models.generate_content(
        model=_model,
        contents=f"セグメント「{bucket}」向けのメールパターンを作成してください。",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=_PatternSchema,
        ),
    )
    record_llm_response(space, _model, response)
    pattern = _PatternSchema.model_validate_json(response.text)
    return DeliverablePattern(
        pattern_id=f"{bucket}__{output_format}",
        segment_id=segment.segment_id,
        bucket=bucket,
        format=output_format,
        subject=pattern.subject,
        blocks=[DeliverableBlock(**b.model_dump()) for b in pattern.blocks],
        created_at=datetime.now(UTC).isoformat(),
    )


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
    output_format: str = "EMAIL",
) -> int:
    """割り当てとパターンから各 Person の Deliverable を組み立てて保存する。"""
    assignments = [
        a.to_dict()
        for a in db.collection(
            f"segments/{segment.segment_id}/snapshots/{snapshot_id}/assignments"
        ).get()
    ]
    run = MarketingRun(
        run_id=run_id,
        status="running",
        segment_id=segment.segment_id,
        snapshot_id=snapshot_id,
        purpose=segment.purpose,
        total=len(assignments),
        created_at=datetime.now(UTC).isoformat(),
    )
    db.collection("marketing_runs").document(run_id).set(run.model_dump())

    done = 0
    for asn in assignments:
        person_id = asn.get("person_id", "")
        bucket = asn.get("bucket", "")
        # パターンキーは "{bucket}__{format}" 規約（generate_patterns と一致）
        pattern_key = f"{bucket}__{output_format}"
        pattern = patterns.get(pattern_key)
        if not pattern:
            logger.warning("no pattern for key '%s' (person %s)", pattern_key, person_id)
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
            format=pattern.get("format", output_format),
            bucket=bucket,
            subject=_fill(pattern.get("subject", ""), fill_ctx),
            blocks=blocks,
            created_at=datetime.now(UTC).isoformat(),
        )
        db.collection(f"marketing_runs/{run_id}/deliverables").document(deliverable_id).set(
            deliverable.model_dump()
        )
        done += 1

    db.collection("marketing_runs").document(run_id).update(
        {"status": "done", "done": done, "deliverable_count": done}
    )
    logger.info(
        "assembly completed: run_id=%s segment=%s done=%d", run_id, segment.segment_id, done
    )
    return done
