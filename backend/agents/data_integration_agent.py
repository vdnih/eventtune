"""
DataIntegrationAgent — Layer 1: データ統合パイプライン（ADR-015 / docs/INGESTION_MAPPING.md）

8ステージ: Read（着地）→ Understand（AI×1回で BatchPlan 生成）→ Confirm（ルーター/UI の
ゲート。承認済み BatchPlan がそのまま実行される）→ Interpret（承認済み仕様の機械適用。
ai_parse 宣言列のみ軽量 AI）→ Conform（マスタ確定）→ Bind（ファクト結合。イベントリンクは
行の列値 → 確認済み既定イベント → 保留）→ Derive（person appeal 集約）→ Report（P1 集計 +
AI 整形 Markdown）。

責務の割り方は「AI か Python か」ではなく実行形態（INGESTION_MAPPING §3）:
骨格・永続・名寄せ = 基盤コード（P1）/ 変換 = AI 生成仕様の機械適用（P3）/
仕様で表現できない列のみ宣言された範囲の AI 行抽出。全観測ブロックは source_records に
着地し、行き先（bound / pending / skipped + 理由）が必ず記録される（黙って捨てない）。
"""

import asyncio
import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from google import genai
from google.cloud.firestore import FieldFilter
from google.genai import types
from pydantic import BaseModel, create_model

import semantic_search
from config import get_settings
from genai_client import new_client
from ingestion import engine, prompts, readers
from ingestion.engine import InterpretedRow, _enum_default, enum_fields_of
from ingestion.normalize import _normalize_name
from ingestion.specs import REGISTRY, IngestionSpec, file_target_kinds
from metering import record_llm_response
from ontology import (
    BatchPlan,
    ContactStage,
    DefaultEventPlan,
    EntityTransformation,
    EventAttendance,
    FilePlan,
    Person,
    ProductInterest,
    SkippedRecord,
    SourceRecord,
    TargetPlan,
)
from space import SpaceContext

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = new_client()
    return _client


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class UnderstandError(RuntimeError):
    """Understand（BatchPlan 生成）の失敗。空プランで黙って続行しない（§4 ステージ2）。"""


# ── Understand: バッチ横断理解（AI フルモデル × 1回 → BatchPlan）──────────────────


def _files_block(files: list[tuple[str, bytes]]) -> str:
    """全ファイルのヘッダー+サンプル（表）/ 冒頭（文書）をプロンプト用に描画する。"""
    parts: list[str] = []
    for filename, content in files:
        if readers.is_tabular(filename):
            try:
                headers, rows = readers.read_tabular(filename, content)
                sample = json.dumps(rows[:5], ensure_ascii=False)
                parts.append(f"--- {filename} ---\nヘッダー: {headers}\nサンプル: {sample}")
            except Exception as e:
                parts.append(f"--- {filename} ---\n読み込みエラー: {e}")
        else:
            try:
                text = readers.read_document_text(filename, content)
                parts.append(f"--- {filename} ---\n{text[:800]}")
            except Exception as e:
                parts.append(f"--- {filename} ---\n読み込みエラー: {e}")
    return "\n\n".join(parts)


def _parse_batch_plan(raw: dict) -> BatchPlan:
    """Understand の JSON 出力を BatchPlan に検証する。未知の entity_type は捨てて警告。"""
    valid_kinds = set(file_target_kinds())
    default_event = None
    de = raw.get("default_event")
    if isinstance(de, dict) and str(de.get("name") or "").strip():
        default_event = DefaultEventPlan(
            name=str(de["name"]).strip(), evidence=str(de.get("evidence") or "")
        )
    file_plans: list[FilePlan] = []
    for f in raw.get("files") or []:
        if not isinstance(f, dict) or not f.get("filename"):
            continue
        targets: list[TargetPlan] = []
        for t in f.get("targets") or []:
            if not isinstance(t, dict):
                continue
            kind = str(t.get("entity_type") or "")
            if kind not in valid_kinds:
                logger.warning(
                    "understand: 未知の entity_type '%s' を無視 (%s)", kind, f["filename"]
                )
                continue
            targets.append(
                TargetPlan(
                    entity_type=kind,
                    column_map={str(k): str(v) for k, v in (t.get("column_map") or {}).items()},
                    column_modes={str(k): str(v) for k, v in (t.get("column_modes") or {}).items()},
                    link_columns={str(k): str(v) for k, v in (t.get("link_columns") or {}).items()},
                )
            )
        file_plans.append(
            FilePlan(
                filename=str(f["filename"]),
                business_context=str(f.get("business_context") or ""),
                targets=targets,
                unmapped_notes=str(f.get("unmapped_notes") or ""),
            )
        )
    return BatchPlan(default_event=default_event, files=file_plans)


async def understand_batch(
    files: list[tuple[str, bytes]],
    hint: str | None,
    existing_event_names: list[str],
    space: SpaceContext | None = None,
) -> BatchPlan:
    """バッチ内全ファイルを1回のフルモデル呼び出しで理解し、BatchPlan（変換仕様）を生成する。"""
    if not files:
        return BatchPlan()
    prompt = prompts.render_understand_prompt(_files_block(files), existing_event_names, hint)
    try:
        _model = get_settings().model_ingestion
        response = await _get_client().aio.models.generate_content(
            model=_model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        if space is not None:
            record_llm_response(space, _model, response)
        raw = json.loads(response.text)
        if not isinstance(raw, dict):
            raise ValueError("BatchPlan の JSON がオブジェクトでない")
        plan = _parse_batch_plan(raw)
    except Exception as e:
        logger.exception("understand_batch failed")
        raise UnderstandError(f"取り込みプランの生成に失敗しました: {e}") from e
    logger.info(
        "understand_batch: files=%s planned=%s default_event=%s",
        [f for f, _ in files],
        [fp.filename for fp in plan.files],
        plan.default_event.name if plan.default_event else None,
    )
    return plan


# ── Interpret: 文書抽出（スペック導出スキーマ）・ai_parse 列の行単位抽出 ──────────────


def _document_response_model(kinds: list[str]) -> type[BaseModel]:
    """FilePlan.targets の種別から、文書抽出のレスポンススキーマを組み立てる。"""
    fields: dict[str, Any] = {}
    for k in kinds:
        spec = REGISTRY[k]
        if spec.observation is None:
            continue
        if spec.role == "patch":
            fields[k] = (spec.observation | None, None)
        else:
            fields[k] = (list[spec.observation], [])
    return create_model("_DocumentResponse", **fields)


async def run_document_extractor(
    text: str,
    target_kinds: list[str],
    business_context: str,
    space: SpaceContext | None = None,
) -> dict[str, list[dict]]:
    """文書1件から観測を抽出する（フルモデル×1回）。戻り値は {kind: [observation dict]}。"""
    kinds = [k for k in target_kinds if k in REGISTRY and REGISTRY[k].observation is not None]
    if not kinds:
        return {}
    response_model = _document_response_model(kinds)
    prompt = prompts.render_document_extractor_prompt(kinds, business_context, text)
    _model = get_settings().model_ingestion
    response = await _get_client().aio.models.generate_content(
        model=_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=response_model
        ),
    )
    if space is not None:
        record_llm_response(space, _model, response)
    parsed = response_model.model_validate_json(response.text)
    out: dict[str, list[dict]] = {}
    for kind in kinds:
        value = getattr(parsed, kind, None)
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        dumps = [v.model_dump() for v in items]
        if dumps:
            out[kind] = dumps
    return out


async def _ai_parse_cells(
    spec: IngestionSpec,
    business_context: str,
    allowed_fields: list[str],
    cells: dict[str, str],
    space: SpaceContext | None = None,
) -> dict | None:
    """ai_parse 宣言列のセル群から、許可フィールドのみを軽量モデルで抽出する。"""
    prompt = prompts.render_ai_parse_prompt(spec, business_context, allowed_fields, cells)
    try:
        _model = get_settings().model_batch
        response = await _get_client().aio.models.generate_content(
            model=_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=spec.observation
            ),
        )
        if space is not None:
            record_llm_response(space, _model, response)
        obs = json.loads(response.text)
        if not isinstance(obs, dict):
            return None
        # 宣言された範囲（ai_parse 列が写る先）以外は捨てる
        return {k: v for k, v in obs.items() if k in allowed_fields}
    except Exception:
        logger.exception("_ai_parse_cells failed: cells=%s", str(cells)[:200])
        return None


# ── 同一性解決（EntityResolver。ADR-011 のまま。曖昧一致の根拠ログのみ拡張）────────


class EntityResolver:
    """種別ごとに {正規化キー: uuid} を保持し、名前から既存/新規の UUID を解決する。

    seed: 既存マスタの [(自然キー文字列, uuid)]。コンストラクタでスペースから読み込む。
    fuzzy=True のときのみ包含一致のフォールバックを行う（マスタ向け。person/fact は False）。
    log には新規採番（resolved_by=created）と包含一致（resolved_by=containment）を残す。
    """

    def __init__(self, kind: str, seed: list[tuple[str, str]], id_prefix: str, fuzzy: bool = True):
        self.kind = kind
        self._id_prefix = id_prefix
        self._fuzzy = fuzzy
        self._by_key: dict[str, str] = {}
        self._entries: list[tuple[str, str]] = []  # (norm_key, uuid)
        for name, _id in seed:
            self._index(name, _id)
        self.created: dict[str, str] = {}  # uuid -> display name（新規採番分）
        self.log: list[dict] = []  # resolved_links 監査用

    def _index(self, name: str, _id: str) -> None:
        key = _normalize_name(name)
        if key and key not in self._by_key:
            self._by_key[key] = _id
            self._entries.append((key, _id))

    def resolve(self, name: str, display: str = "") -> tuple[str | None, bool]:
        """名前 → (uuid, created)。名前が空なら (None, False)。

        完全一致（正規化）→ 一意な包含一致（fuzzy 時）→ 新規 UUID 採番。
        """
        key = _normalize_name(name)
        if not key:
            return None, False
        if key in self._by_key:
            return self._by_key[key], False
        if self._fuzzy:
            cands = {_id for k, _id in self._entries if k and (k in key or key in k)}
            if len(cands) == 1:
                _id = next(iter(cands))
                self._by_key[key] = _id  # 以降の照合を高速化
                self.log.append(
                    {
                        "kind": self.kind,
                        "id": _id,
                        "name": display or name,
                        "resolved_by": "containment",
                    }
                )
                return _id, False
        new_id = _new_id(self._id_prefix)
        self._by_key[key] = new_id
        self._entries.append((key, new_id))
        self.created[new_id] = display or name
        self.log.append(
            {"kind": self.kind, "id": new_id, "name": display or name, "resolved_by": "created"}
        )
        return new_id, True


def _load_existing(
    db: Any, collection: str, name_field: str, id_field: str
) -> list[tuple[str, str]]:
    """既存マスタを [(name, uuid)] で読み込み、resolver のシードにする。"""
    out: list[tuple[str, str]] = []
    try:
        for doc in db.collection(collection).get():
            d = doc.to_dict() or {}
            name = d.get(name_field, "")
            _id = d.get(id_field) or doc.id
            if name and _id:
                out.append((name, _id))
    except Exception:
        logger.exception("_load_existing failed: collection=%s", collection)
    return out


def _person_key(name: str, email: str, company: str) -> str:
    """person の自然キー: 正規化 email、無ければ 正規化(name)|正規化(company)。"""
    em = _normalize_name(email)
    if em:
        return em
    return f"{_normalize_name(name)}|{_normalize_name(company)}"


def _load_existing_persons(db: Any) -> list[tuple[str, str]]:
    """既存 person を [(自然キー, uuid)] で読み込む（email 優先、無ければ name）。"""
    out: list[tuple[str, str]] = []
    try:
        for doc in db.collection("persons").get():
            d = doc.to_dict() or {}
            pid = d.get("person_id") or doc.id
            if not pid:
                continue
            key = _person_key(d.get("name", ""), d.get("email") or "", "")
            if key.strip("|"):
                out.append((key, pid))
    except Exception:
        logger.exception("_load_existing_persons failed")
    return out


def _build_resolvers(db: Any) -> dict[str, EntityResolver]:
    """マスタ種別ごとの resolver をレジストリ駆動で構築する。"""
    resolvers: dict[str, EntityResolver] = {}
    for kind, spec in REGISTRY.items():
        if spec.role != "master":
            continue
        if kind == "persons":
            seed = _load_existing_persons(db)
        else:
            seed = _load_existing(db, spec.collection, spec.natural_key[0], spec.id_field)
        resolvers[kind] = EntityResolver(kind, seed, spec.id_prefix, fuzzy=spec.fuzzy)
    return resolvers


# ── source_records の行き先追跡 ──────────────────────────────────────────────────


@dataclass
class _Source:
    """着地済みの観測ブロック1件（source_records のメモリ上の対応物）。"""

    record_id: str
    filename: str
    row_no: int
    raw: dict
    bound_refs: dict[str, list[str]] = field(default_factory=dict)
    pending_reasons: list[str] = field(default_factory=list)
    skipped_reasons: list[str] = field(default_factory=list)

    def bind(self, ref_kind: str, ref_id: str) -> None:
        self.bound_refs.setdefault(ref_kind, []).append(ref_id)

    def status(self) -> tuple[str, str]:
        """最終ステータス。保留 > 結合済み > スキップ の優先で決める。"""
        if self.pending_reasons:
            return "pending", " / ".join(dict.fromkeys(self.pending_reasons))
        if self.bound_refs:
            return "bound", ""
        reasons = " / ".join(dict.fromkeys(self.skipped_reasons)) or "解釈結果なし"
        return "skipped", reasons


# ── パイプライン本体 ─────────────────────────────────────────────────────────────


@dataclass
class BatchResult:
    """process_batch の結果（バッチ doc にも同内容を書き込む）。"""

    created_entities: dict[str, int] = field(default_factory=dict)
    pending_count: int = 0
    skipped_count: int = 0
    report_markdown: str = ""


def _heartbeat(db: Any, batch_id: str, stage: str) -> None:
    """ステージ毎の進捗を刻む（stale sweep の生存信号）。"""
    try:
        db.document(f"integration_jobs/{batch_id}").set(
            {"stage": stage, "heartbeat_at": _now_iso()}, merge=True
        )
    except Exception:
        logger.exception("heartbeat failed: batch_id=%s stage=%s", batch_id, stage)


async def _read_stage(
    db: Any, space_id: str, batch_id: str, files: list[tuple[str, bytes]]
) -> dict[str, list[_Source]]:
    """Read: 全ファイルを観測ブロックに変換し source_records に着地させる。"""
    by_file: dict[str, list[_Source]] = {}
    for filename, content in files:
        sources: list[_Source] = []
        for block in readers.read_blocks(filename, content):
            rid = _new_id("src_")
            src = _Source(record_id=rid, filename=filename, row_no=block.row_no, raw=block.raw)
            if block.read_error:
                src.skipped_reasons.append(block.read_error)
            record = SourceRecord(
                record_id=rid,
                space_id=space_id,
                batch_id=batch_id,
                filename=filename,
                row_no=block.row_no,
                raw=block.raw,
                status="pending",
                reason="処理中",
                created_at=_now_iso(),
            )
            db.document(f"source_records/{rid}").set(record.model_dump())
            sources.append(src)
        by_file[filename] = sources
    return by_file


async def _merge_ai_parse_columns(
    spec: IngestionSpec,
    business_context: str,
    ai_cols: list[str],
    allowed_fields: list[str],
    rows: list[dict],
    irows: list[InterpretedRow],
    space: SpaceContext | None,
) -> None:
    """ai_parse 宣言列を行単位で軽量 AI 抽出し、機械適用済みの行へマージする。"""
    sem = asyncio.Semaphore(20)

    async def _one(row_raw: dict, irow: InterpretedRow) -> None:
        cells = {c: str(row_raw.get(c, "")).strip() for c in ai_cols}
        cells = {c: v for c, v in cells.items() if v}
        if not cells:
            return
        async with sem:
            obs = await _ai_parse_cells(spec, business_context, allowed_fields, cells, space=space)
        if obs:
            engine.merge_observation(irow, spec, obs)

    await asyncio.gather(*[_one(r, ir) for r, ir in zip(rows, irows, strict=True)])


async def _interpret_tabular_file(
    fp: FilePlan,
    sources: list[_Source],
    space: SpaceContext | None,
) -> list[tuple[_Source, InterpretedRow]]:
    """表形式1ファイルの解釈: 承認済み仕様の機械適用 + ai_parse 宣言列のみ軽量 AI。"""
    rows = [s.raw for s in sources if not s.skipped_reasons]
    live_sources = [s for s in sources if not s.skipped_reasons]
    out: list[tuple[_Source, InterpretedRow]] = []
    for target in fp.targets:
        spec = REGISTRY[target.entity_type]
        irows = engine.interpret_rows(spec, target, rows)
        ai_cols = [
            c
            for c, mode in target.column_modes.items()
            if mode == engine.AI_PARSE and c in target.column_map
        ]
        if ai_cols:
            allowed = sorted({target.column_map[c] for c in ai_cols})
            await _merge_ai_parse_columns(
                spec, fp.business_context, ai_cols, allowed, rows, irows, space
            )
        out.extend(zip(live_sources, irows, strict=True))
    return out


async def _interpret_text_file(
    fp: FilePlan,
    sources: list[_Source],
    space: SpaceContext | None,
) -> list[tuple[_Source, InterpretedRow]]:
    """文書1ファイルの解釈: スペック導出スキーマで抽出 → 同一経路（interpret_observation）へ。"""
    if not sources or sources[0].skipped_reasons:
        return []
    source = sources[0]
    text = str(source.raw.get("text", ""))
    kinds = [t.entity_type for t in fp.targets]
    try:
        obs_map = await run_document_extractor(text, kinds, fp.business_context, space=space)
    except Exception as e:
        logger.exception("document extractor failed: file=%s", fp.filename)
        source.skipped_reasons.append(f"文書抽出に失敗: {str(e)[:200]}")
        return []
    out: list[tuple[_Source, InterpretedRow]] = []
    # 同一文書に単一イベントが記載されている場合、そのイベントを同文書内の観測のリンク既定にする
    # （文書内の文脈による決定論的な補完。バッチ横断のフォールバックではない）
    event_names = [str(o.get("name") or "").strip() for o in obs_map.get("events", [])]
    event_names = [n for n in event_names if n]
    doc_event = event_names[0] if len(event_names) == 1 else ""
    for kind, obs_list in obs_map.items():
        spec = REGISTRY[kind]
        for obs in obs_list:
            row = engine.interpret_observation(spec, obs)
            if "event" in spec.links and not row.links.get("event") and doc_event:
                row.links["event"] = doc_event
            out.append((source, row))
    return out


def _fill_required_fields(spec: IngestionSpec, payload: dict) -> None:
    """モデルの必須フィールドの欠けを既定値で埋める（str→空文字、Enum→既定メンバー）。"""
    enum_fields = enum_fields_of(spec.model)
    for fname, fobj in spec.model.model_fields.items():
        if not fobj.is_required() or fname in payload:
            continue
        if fname in (spec.id_field, "space_id"):
            continue
        if fname in enum_fields:
            payload[fname] = _enum_default(spec, fname, enum_fields[fname])
        elif fobj.annotation is str:
            payload[fname] = ""


def _appeal_payload(spec: IngestionSpec, entity: BaseModel) -> tuple[str, dict] | None:
    if spec.appeal is None:
        return None
    payload = {}
    for f in spec.appeal.payload_fields:
        v = getattr(entity, f, "")
        payload[f] = v.value if hasattr(v, "value") else v
    return spec.appeal.kind, payload


async def _conform_masters(
    db: Any,
    space: SpaceContext | None,
    interpreted: list[tuple[_Source, InterpretedRow]],
    default_event: str,
    resolvers: dict[str, EntityResolver],
    counts: Counter,
) -> None:
    """Conform: マスタを実在検索 find-or-create で確定・永続する（レジストリ駆動）。

    詳細レコード（マスタ行）・パッチ（KPI/アンケート）・観測からの参照名を同一 UUID に畳み、
    1 マスタ 1 回だけ永続 + appeal 生成する。
    """
    space_id = space.space_id if space is not None else ""
    master_kinds = [k for k, s in REGISTRY.items() if s.role == "master" and k != "persons"]
    masters: dict[str, dict[str, dict]] = {k: {} for k in master_kinds}

    def _resolve_into(kind: str, name: str) -> str | None:
        uid, _ = resolvers[kind].resolve(name, display=name)
        return uid

    # 1) マスタ詳細行（フルの payload）+ パッチ行（イベントへの畳み込み）
    for src, row in interpreted:
        if row.skip_reason:
            src.skipped_reasons.append(row.skip_reason)
            continue
        spec = REGISTRY[row.kind]
        if spec.role == "master" and row.kind in masters:
            name = str(row.data.get(spec.natural_key[0]) or "")
            uid = _resolve_into(row.kind, name)
            if not uid:
                continue
            payload = dict(row.data)
            # マスタのリンク（contents→event 等）は linked_{kind}_id へ解決
            for link_kind in spec.links:
                link_name = row.links.get(link_kind)
                if isinstance(link_name, str) and link_name:
                    linked_field = f"linked_{link_kind}_id"
                    if linked_field in spec.model.model_fields:
                        payload[linked_field] = _resolve_into(
                            spec.links[link_kind].target, link_name
                        )
            masters[row.kind].setdefault(uid, {}).update(payload)
            src.bind(row.kind, uid)
        elif spec.role == "patch":
            ev_name = (row.links.get("event") or "") or default_event
            if not ev_name:
                src.pending_reasons.append(
                    f"イベントリンク未解決（{row.kind} の畳み込み先が決められない）"
                )
                continue
            ev_id = _resolve_into("events", str(ev_name))
            if not ev_id:
                continue
            patch = {k: v for k, v in row.data.items() if v is not None}
            patch["updated_at"] = _now_iso()
            masters["events"].setdefault(ev_id, {}).update(patch)
            src.bind("events", ev_id)

    # 2) 観測・費用からの参照名 → 最低限のマスタを保証（find-or-create の発見）
    def _ensure(kind: str, name: str) -> None:
        if not name:
            return
        spec = REGISTRY[kind]
        uid = _resolve_into(kind, name)
        if uid:
            masters[kind].setdefault(uid, {}).setdefault(spec.natural_key[0], name)

    if default_event:
        _ensure("events", default_event)  # 確認済み既定イベントは必ず実在させる
    for _src, row in interpreted:
        if row.skip_reason or REGISTRY[row.kind].role == "master":
            continue
        ev = row.links.get("event") or ""
        if isinstance(ev, str):
            _ensure("events", ev)
        account = row.links.get("account") or ""
        if isinstance(account, str):
            _ensure("accounts", account)
        for pn in row.links.get("product") or []:
            _ensure("products", pn)

    # 3) エンティティ構築 → appeal 生成（並列）→ 永続
    pending: list[tuple[IngestionSpec, str, BaseModel, tuple[str, dict] | None]] = []
    for kind, items in masters.items():
        spec = REGISTRY[kind]
        for uid, payload in items.items():
            payload.setdefault("created_at", _now_iso())
            _fill_required_fields(spec, payload)
            try:
                entity = spec.model(**{spec.id_field: uid, "space_id": space_id, **payload})
            except Exception:
                logger.exception(
                    "conform build failed: kind=%s uid=%s payload=%s", kind, uid, payload
                )
                continue
            pending.append((spec, uid, entity, _appeal_payload(spec, entity)))

    async def _appeal_or_empty(spec_payload: tuple[str, dict] | None) -> tuple[str, list[float]]:
        if not spec_payload:
            return "", []
        return await semantic_search.build_appeal(spec_payload[0], spec_payload[1], space=space)

    appeals = await asyncio.gather(*[_appeal_or_empty(ap) for *_, ap in pending])
    for (spec, uid, entity, ap), (summary, vector) in zip(pending, appeals, strict=True):
        if ap:
            entity.appeal_summary = summary
            entity.appeal_vector = vector
        db.document(f"{spec.collection}/{uid}").set(entity.model_dump(), merge=True)
        counts[spec.kind] += 1
    logger.info("conform done: %s", {k: len(v) for k, v in masters.items()})


async def _bind_facts(
    db: Any,
    space: SpaceContext | None,
    interpreted: list[tuple[_Source, InterpretedRow]],
    resolvers: dict[str, EntityResolver],
    default_event: str,
    batch_id: str,
    counts: Counter,
) -> set[str]:
    """Bind: person を確定し、ファクトを確定済み UUID へ束ねて永続する。

    イベントリンクの優先順位: 行の列値（row.links）→ 確認済み既定イベント → 保留（pending）。
    未解決はファクトを書かず source_record に理由を残す（黙って捨てない。person 自体は作る）。
    """
    space_id = space.space_id if space is not None else ""
    person_res = resolvers["persons"]
    # ファクトの冪等性: バッチ内の重複生成を (person,event,action) / (person,product) で抑止
    att_res = EntityResolver("attendance", [], "att_", fuzzy=False)
    int_res = EntityResolver("interest", [], "int_", fuzzy=False)
    touched: set[str] = set()

    def _event_id_for(row: InterpretedRow) -> str | None:
        ev_name = str(row.links.get("event") or "") or default_event
        if not ev_name:
            return None
        return resolvers["events"].resolve(ev_name)[0]

    for src, row in interpreted:
        if row.skip_reason:
            continue  # conform で記録済み
        spec = REGISTRY[row.kind]
        if spec.role != "fact":
            continue
        now = _now_iso()

        if row.kind == "event_attendances":
            company = str(row.links.get("account") or "")
            account_id = resolvers["accounts"].resolve(company)[0] if company else None
            pkey = _person_key(
                str(row.data.get("name") or ""), str(row.data.get("email") or ""), company
            )
            pid, _created = person_res.resolve(pkey, display=str(row.data.get("name") or ""))
            if not pid:
                src.skipped_reasons.append("person の自然キーが空")
                continue
            person = Person(
                person_id=pid,
                space_id=space_id,
                account_id=account_id,
                name=str(row.data.get("name") or ""),
                email=(row.data.get("email") or None),
                department=str(row.data.get("department") or ""),
                job_title=str(row.data.get("job_title") or ""),
                stage=ContactStage.LEAD,
                source_job_id=batch_id,
                created_at=now,
            )
            # appeal_* は導出ステージで全 attendance を集約して付与する（ここでは温存）
            db.document(f"persons/{pid}").set(
                person.model_dump(exclude={"appeal_summary", "appeal_vector"}), merge=True
            )
            touched.add(pid)
            counts["persons"] += 1
            src.bind("persons", pid)

            ev_id = _event_id_for(row)
            if ev_id:
                aid, _ = att_res.resolve(f"{pid}|{ev_id}|{row.data.get('action_type') or '参加'}")
                att = EventAttendance(
                    attendance_id=aid,
                    space_id=space_id,
                    person_id=pid,
                    event_id=ev_id,
                    action_type=str(row.data.get("action_type") or "参加"),
                    owner_staff=str(row.data.get("owner_staff") or ""),
                    challenge_note=str(row.data.get("challenge_note") or ""),
                    memo=str(row.data.get("memo") or ""),
                    source_job_id=batch_id,
                    created_at=now,
                )
                db.document(f"event_attendances/{aid}").set(att.model_dump(), merge=True)
                counts["event_attendances"] += 1
                src.bind("event_attendances", aid)
            else:
                src.pending_reasons.append("イベントリンク未解決（行に列値なし・既定イベントなし）")

            for pn in row.links.get("product") or []:
                pr_id = resolvers["products"].resolve(pn)[0]
                if not pr_id:
                    continue
                iid, _ = int_res.resolve(f"{pid}|{pr_id}")
                pi = ProductInterest(
                    interest_id=iid,
                    space_id=space_id,
                    person_id=pid,
                    product_id=pr_id,
                    source_job_id=batch_id,
                    created_at=now,
                )
                db.document(f"product_interests/{iid}").set(pi.model_dump(), merge=True)
                counts["product_interests"] += 1
                src.bind("product_interests", iid)
        else:
            # 汎用ファクト（cost_items 等）: リンクを解決してモデルへ
            ev_id = _event_id_for(row) if "event" in spec.links else None
            if "event" in spec.links and spec.links["event"].required and not ev_id:
                src.pending_reasons.append(
                    f"イベントリンク未解決（{row.kind} の紐づけ先が決められない）"
                )
                continue
            fact_id = _new_id(spec.id_prefix)
            payload = dict(row.data)
            _fill_required_fields(spec, payload)
            try:
                fact = spec.model(
                    **{
                        spec.id_field: fact_id,
                        "space_id": space_id,
                        "event_id": ev_id,
                        "source_job_id": batch_id,
                        "created_at": now,
                        **payload,
                    }
                )
            except Exception:
                logger.exception("fact build failed: kind=%s payload=%s", row.kind, payload)
                src.skipped_reasons.append(f"{row.kind} の構築に失敗")
                continue
            db.document(f"{spec.collection}/{fact_id}").set(fact.model_dump(), merge=True)
            counts[row.kind] += 1
            src.bind(row.kind, fact_id)

    logger.info("bind done: touched_persons=%d", len(touched))
    return touched


# ── Derive: person.appeal を全 attendance から集約再生成（ADR-011 のまま）───────────


def _person_appeal_payload(person: dict, encounters: list[dict], interests: list[str]) -> dict:
    enc_lines: list[str] = []
    for e in encounters:
        parts = [
            f"イベント: {e['event']}" if e.get("event") else "",
            f"日時: {e['date']}" if e.get("date") else "",
            f"接客担当: {e['owner_staff']}" if e.get("owner_staff") else "",
            f"課題感: {e['challenge_note']}" if e.get("challenge_note") else "",
            f"メモ: {e['memo']}" if e.get("memo") else "",
        ]
        line = " / ".join(p for p in parts if p)
        if line:
            enc_lines.append(line)
    return {
        "name": person.get("name", ""),
        "department": person.get("department", ""),
        "job_title": person.get("job_title", ""),
        "接客履歴": "\n".join(enc_lines),
        "関心製品": ", ".join(interests),
    }


async def _derive_person_appeal(db: Any, space: SpaceContext | None, person_ids: set[str]) -> None:
    """touched person の appeal_summary / appeal_vector を全 attendance から集約再生成する。"""
    if not person_ids:
        return

    events_map: dict[str, dict] = {}
    products_map: dict[str, dict] = {}
    try:
        for doc in db.collection("events").get():
            d = doc.to_dict() or {}
            events_map[d.get("event_id") or doc.id] = d
    except Exception:
        logger.exception("derive: load events failed")
    try:
        for doc in db.collection("products").get():
            d = doc.to_dict() or {}
            products_map[d.get("product_id") or doc.id] = d
    except Exception:
        logger.exception("derive: load products failed")

    sem = asyncio.Semaphore(8)

    async def _one(pid: str) -> None:
        async with sem:
            pdoc = db.document(f"persons/{pid}").get()
            if not getattr(pdoc, "exists", False):
                return
            person = pdoc.to_dict() or {}

            encounters: list[dict] = []
            try:
                atts = (
                    db.collection("event_attendances")
                    .where(filter=FieldFilter("person_id", "==", pid))
                    .get()
                )
            except Exception:
                atts = []
            for a in atts:
                ad = a.to_dict() or {}
                ev = events_map.get(ad.get("event_id"), {})
                encounters.append(
                    {
                        "event": ev.get("name", ""),
                        "date": ev.get("event_date", ""),
                        "owner_staff": ad.get("owner_staff", ""),
                        "challenge_note": ad.get("challenge_note", ""),
                        "memo": ad.get("memo", ""),
                    }
                )

            interests: list[str] = []
            try:
                pis = (
                    db.collection("product_interests")
                    .where(filter=FieldFilter("person_id", "==", pid))
                    .get()
                )
            except Exception:
                pis = []
            for pi in pis:
                pd = pi.to_dict() or {}
                pr = products_map.get(pd.get("product_id"), {})
                if pr.get("product_name"):
                    interests.append(pr["product_name"])

            payload = _person_appeal_payload(person, encounters, interests)
            summary, vector = await semantic_search.build_appeal("person", payload, space=space)
            db.document(f"persons/{pid}").set(
                {"appeal_summary": summary, "appeal_vector": vector}, merge=True
            )

    await asyncio.gather(*[_one(pid) for pid in person_ids])
    logger.info("derive done: persons=%d", len(person_ids))


# ── Report: P1 集計 + AI 整形 Markdown ─────────────────────────────────────────────


async def _render_report(aggregate: dict, space: SpaceContext | None) -> str:
    """集計を AI で Markdown に整形する。失敗時は素の集計 JSON を返す（事実は失わない）。"""
    try:
        _model = get_settings().model_ingestion
        response = await _get_client().aio.models.generate_content(
            model=_model, contents=prompts.render_report_prompt(aggregate)
        )
        if space is not None:
            record_llm_response(space, _model, response)
        text = (response.text or "").strip()
        if text:
            return text
    except Exception:
        logger.exception("_render_report failed")
    return "```json\n" + json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n```"


# ── バッチ処理（依存順の多段オーケストレーション）─────────────────────────────────


async def process_batch(
    files: list[tuple[str, bytes]],
    batch_id: str,
    db: Any,  # space.ScopedClient（スペース前置済み）
    plan: BatchPlan,
    space: SpaceContext | None = None,
) -> BatchResult:
    """承認済み BatchPlan（変換仕様）をそのまま実行する（承認と実行の一致。ADR-015）。

    Read → Interpret → Conform → Bind → Derive → Report。各段頭でハートビートを刻み、
    全観測ブロックの行き先（bound / pending / skipped + 理由）を source_records に確定する。
    """
    space_id = space.space_id if space is not None else ""
    default_event = plan.default_event.name.strip() if plan.default_event else ""
    counts: Counter = Counter()

    # 1) Read: 観測ブロックへ変換し source_records に着地
    _heartbeat(db, batch_id, "read")
    by_file = await _read_stage(db, space_id, batch_id, files)

    # 2) Interpret: 承認済み仕様の機械適用（ai_parse 宣言列と文書のみ AI）
    _heartbeat(db, batch_id, "interpret")
    plans_by_file = {fp.filename: fp for fp in plan.files}
    interpreted: list[tuple[_Source, InterpretedRow]] = []
    for filename, sources in by_file.items():
        fp = plans_by_file.get(filename)
        if fp is None or not fp.targets:
            for s in sources:
                s.skipped_reasons.append("変換仕様なし（承認済みプランに含まれないファイル）")
            continue
        if readers.is_tabular(filename):
            interpreted.extend(await _interpret_tabular_file(fp, sources, space))
        else:
            interpreted.extend(await _interpret_text_file(fp, sources, space))

    # 3) Conform → 4) Bind → 5) Derive（依存順。マスタ確定がファクト結合に先行する）
    _heartbeat(db, batch_id, "conform")
    resolvers = _build_resolvers(db)
    await _conform_masters(db, space, interpreted, default_event, resolvers, counts)

    _heartbeat(db, batch_id, "bind")
    touched = await _bind_facts(db, space, interpreted, resolvers, default_event, batch_id, counts)

    _heartbeat(db, batch_id, "derive")
    await _derive_person_appeal(db, space, touched)

    # 6) Report: 行き先の確定・集計・AI 整形
    _heartbeat(db, batch_id, "report")
    pending_count = 0
    skipped_count = 0
    pending_details: list[dict] = []
    skipped_records: list[SkippedRecord] = []
    all_sources = [s for sources in by_file.values() for s in sources]
    for src in all_sources:
        status, reason = src.status()
        db.document(f"source_records/{src.record_id}").set(
            {"status": status, "reason": reason, "refs": src.bound_refs}, merge=True
        )
        if status == "pending":
            pending_count += 1
            if len(pending_details) < 50:
                pending_details.append(
                    {"filename": src.filename, "row_no": src.row_no, "reason": reason}
                )
        elif status == "skipped":
            skipped_count += 1
            if len(skipped_records) < 200:
                skipped_records.append(
                    SkippedRecord(
                        entity_type=src.filename, reason=reason, detail=str(src.raw)[:200]
                    )
                )

    resolved_links: list[dict] = []
    new_masters: list[dict] = []
    fuzzy_matches: list[dict] = []
    for r in resolvers.values():
        resolved_links.extend(r.log)
        for entry in r.log:
            if entry.get("resolved_by") == "created" and entry["kind"] != "persons":
                new_masters.append({"kind": entry["kind"], "name": entry["name"]})
            elif entry.get("resolved_by") == "containment":
                fuzzy_matches.append(entry)

    transformations: list[EntityTransformation] = []
    for src, row in interpreted:
        if row.decisions and len(transformations) < 200:
            transformations.append(
                EntityTransformation(
                    entity_type=row.kind,
                    entity_id="",
                    source_label=f"{src.filename}:{src.row_no}",
                    decisions=row.decisions,
                )
            )

    aggregate = {
        "created": dict(counts),
        "pending_count": pending_count,
        "pending": pending_details,
        "skipped_count": skipped_count,
        "skipped": [s.model_dump() for s in skipped_records[:20]],
        "new_masters": new_masters,
        "fuzzy_matches": fuzzy_matches,
        "default_event": plan.default_event.model_dump() if plan.default_event else None,
    }
    report_markdown = await _render_report(aggregate, space)

    db.document(f"integration_jobs/{batch_id}").set(
        {
            "plan": plan.model_dump(),
            "created_entities": dict(counts),
            "pending_count": pending_count,
            "skipped_count": skipped_count,
            "report_markdown": report_markdown,
            "resolved_links": resolved_links,
            "transformations": [t.model_dump() for t in transformations],
            "skipped_records": [s.model_dump() for s in skipped_records],
        },
        merge=True,
    )
    logger.info(
        "process_batch done: batch_id=%s created=%s pending=%d skipped=%d",
        batch_id,
        dict(counts),
        pending_count,
        skipped_count,
    )
    return BatchResult(
        created_entities=dict(counts),
        pending_count=pending_count,
        skipped_count=skipped_count,
        report_markdown=report_markdown,
    )
