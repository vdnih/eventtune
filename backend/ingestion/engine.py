"""
engine — 解釈エンジン（承認済み変換仕様の機械適用。純粋・I/O なし）

「変換仕様の機械適用」の実体は6種別の処理のみ（INGESTION_MAPPING §4）:
  direct コピー / 姓名合成 / N:1 ラベル付き連結 / リンク列の分割 / normalizer / enum 変換

処理種別は observation・モデルの型と宣言から導出され、column_map 側は
「元列→フィールド」を書くだけでよい。全行の行き先（解釈済み / skip+理由）が必ず決まる。
"""

from dataclasses import dataclass, field
from enum import Enum
from functools import cache
from typing import Any, get_args

from pydantic import BaseModel

from ingestion.normalize import _split_names, _to_float, _to_int
from ingestion.specs import NAME_PART_FIELDS, IngestionSpec
from ontology import TargetPlan, TransformDecision

AI_PARSE = "ai_parse"  # TargetPlan.column_modes の値（この列は行ごとに AI で解釈する）


@dataclass
class InterpretedRow:
    """1観測ブロックの解釈結果（永続前の中間レコード）。"""

    kind: str
    data: dict = field(default_factory=dict)  # モデルフィールド値（正規化・enum 変換済み）
    links: dict = field(default_factory=dict)  # {"event": "名前", "product": ["A", "B"]}
    decisions: list[TransformDecision] = field(default_factory=list)
    skip_reason: str | None = None  # 非 None → skipped（理由付き。黙って捨てない）


# ── モデルの型情報（enum / 数値フィールドの導出）────────────────────────────────


@cache
def enum_fields_of(model: type[BaseModel]) -> dict[str, type[Enum]]:
    """モデルの Enum 型フィールド {name: EnumType}（Optional[Enum] も含む）。"""
    out: dict[str, type[Enum]] = {}
    for name, f in model.model_fields.items():
        for t in (f.annotation, *get_args(f.annotation)):
            if isinstance(t, type) and issubclass(t, Enum):
                out[name] = t
                break
    return out


@cache
def _numeric_fields_of(model: type[BaseModel]) -> dict[str, type]:
    """モデルの数値フィールド {name: float|int}（Optional も含む。bool は対象外）。"""
    out: dict[str, type] = {}
    for name, f in model.model_fields.items():
        for t in (f.annotation, *get_args(f.annotation)):
            if t is float or t is int:
                out[name] = t
                break
    return out


def _enum_default(spec: IngestionSpec, fld: str, enum_type: type[Enum]) -> Enum:
    """未知値のときの既定: spec.enum_defaults → モデル既定 → 先頭メンバー。"""
    if fld in spec.enum_defaults and spec.enum_defaults[fld] is not None:
        return spec.enum_defaults[fld]
    f = spec.model.model_fields.get(fld)
    if f is not None and not f.is_required() and isinstance(f.get_default(), enum_type):
        return f.get_default()
    return next(iter(enum_type))


def _coerce_enum(
    spec: IngestionSpec, fld: str, enum_type: type[Enum], raw: Any
) -> tuple[Enum, TransformDecision]:
    """語彙 = Enum 値で照合し、未知値は既定へ。判断は必ず TransformDecision に残す。"""
    raw_str = str(raw).strip()
    value_map = {e.value: e for e in enum_type}
    if isinstance(raw, enum_type):
        member = raw
    else:
        member = value_map.get(raw_str)
    if member is not None:
        reason = f"'{raw_str}' を enum にマッピング"
    else:
        member = _enum_default(spec, fld, enum_type)
        reason = f"未知の値 '{raw_str}' → 既定({member.value})"
    return member, TransformDecision(
        field=fld, value=member.value, reason=reason, source_signals={fld: raw_str}
    )


# ── 値の適用（normalizer / enum / 数値 / direct の共通経路）───────────────────────


def _apply_value(
    spec: IngestionSpec, fld: str, raw: Any, data: dict, decisions: list[TransformDecision]
) -> None:
    enum_fields = enum_fields_of(spec.model)
    numeric_fields = _numeric_fields_of(spec.model)
    if fld in spec.normalizers:
        value, reason = spec.normalizers[fld](raw)
        data[fld] = value
        if reason:
            decisions.append(
                TransformDecision(
                    field=fld, value=str(value), reason=reason, source_signals={fld: str(raw)}
                )
            )
    elif fld in enum_fields:
        value, decision = _coerce_enum(spec, fld, enum_fields[fld], raw)
        data[fld] = value
        decisions.append(decision)
    elif fld in numeric_fields and not isinstance(raw, int | float):
        data[fld] = _to_int(raw) if numeric_fields[fld] is int else _to_float(raw)
    else:
        data[fld] = raw


def _skip(spec: IngestionSpec, data: dict) -> str | None:
    """最小要件チェック。既定はマスタの自然キー先頭が非空であること。"""
    if spec.skip_check is not None:
        return spec.skip_check(data)
    if spec.role == "master" and spec.natural_key:
        key_field = spec.natural_key[0]
        if not str(data.get(key_field) or "").strip():
            return f"{key_field} が空のためスキップ"
    return None


def _synthesize_name(last: str, first: str, data: dict) -> None:
    if (last or first) and not str(data.get("name") or "").strip():
        data["name"] = f"{last} {first}".strip()


# ── 表形式: 承認済み TargetPlan の全行への機械適用 ─────────────────────────────────


def interpret_rows(
    spec: IngestionSpec, target: TargetPlan, rows: list[dict]
) -> list[InterpretedRow]:
    """承認済み変換仕様（TargetPlan）を全行に適用する。戻り値は rows と同順・同数。"""
    link_field_map = {spec.link_obs_field(k): (k, ls) for k, ls in spec.links.items()}
    # 逆引き: フィールド → 元列リスト（N:1 検出。ai_parse 宣言列はここでは扱わない）
    field_sources: dict[str, list[str]] = {}
    for col, fld in target.column_map.items():
        if target.column_modes.get(col) != AI_PARSE:
            field_sources.setdefault(fld, []).append(col)
    name_last_cols = field_sources.pop("name_last", [])
    name_first_cols = field_sources.pop("name_first", [])
    enum_fields = enum_fields_of(spec.model)
    numeric_fields = _numeric_fields_of(spec.model)

    def _first_val(row: dict, cols: list[str]) -> str:
        for c in cols:
            v = str(row.get(c, "")).strip()
            if v:
                return v
        return ""

    out: list[InterpretedRow] = []
    for row in rows:
        data: dict = {}
        links: dict = {}
        decisions: list[TransformDecision] = []
        _synthesize_name(_first_val(row, name_last_cols), _first_val(row, name_first_cols), data)
        for fld, cols in field_sources.items():
            vals = [(c, str(row.get(c, "")).strip()) for c in cols]
            vals = [(c, v) for c, v in vals if v]
            if not vals:
                continue
            if fld in link_field_map:  # リンク列（分割）
                kind, ls = link_field_map[fld]
                links[kind] = _split_names(vals[0][1]) if ls.many else vals[0][1]
            elif len(cols) > 1:  # N:1
                if fld in enum_fields or fld in numeric_fields or fld in spec.normalizers:
                    # 連結できない型は先頭列を採用し、判断を記録する
                    _apply_value(spec, fld, vals[0][1], data, decisions)
                    decisions.append(
                        TransformDecision(
                            field=fld,
                            value=str(data.get(fld, "")),
                            reason=f"複数列が対応づけられたため先頭列 '{vals[0][0]}' を採用",
                            source_signals={c: v for c, v in vals},
                        )
                    )
                else:  # ラベル付き連結（ロスレス）
                    data[fld] = " / ".join(f"{c}: {v}" for c, v in vals)
            else:
                _apply_value(spec, fld, vals[0][1], data, decisions)
        # 行ごとにリンク先が異なる列（link_columns）は列写像より優先
        for kind, col in target.link_columns.items():
            v = str(row.get(col, "")).strip()
            if v:
                ls = spec.links.get(kind)
                links[kind] = _split_names(v) if (ls is not None and ls.many) else v
        out.append(
            InterpretedRow(
                kind=spec.kind,
                data=data,
                links=links,
                decisions=decisions,
                skip_reason=_skip(spec, data),
            )
        )
    return out


# ── 観測 dict（文書抽出 / ai_parse 出力）の解釈 ──────────────────────────────────


def interpret_observation(spec: IngestionSpec, obs: dict) -> InterpretedRow:
    """observation フィールド名でキーされた dict（AI 出力）を解釈する。

    表形式と同じ後段（normalizer・enum 変換・リンク分割）を通すことで、
    文書パス / ai_parse パスの出力も単一の経路で正規化される。
    """
    link_field_map = {spec.link_obs_field(k): (k, ls) for k, ls in spec.links.items()}
    cleaned = {k: v for k, v in obs.items() if v not in (None, "", [])}
    skip_hint = cleaned.pop("skip_reason", None)
    data: dict = {}
    links: dict = {}
    decisions: list[TransformDecision] = []
    _synthesize_name(
        str(cleaned.pop("name_last", "") or ""), str(cleaned.pop("name_first", "") or ""), data
    )
    for fld, val in cleaned.items():
        if fld in NAME_PART_FIELDS:
            continue
        if fld in link_field_map:
            kind, ls = link_field_map[fld]
            if ls.many:
                links[kind] = (
                    [str(v).strip() for v in val]
                    if isinstance(val, list)
                    else _split_names(str(val))
                )
            else:
                links[kind] = str(val).strip()
        else:
            _apply_value(spec, fld, val, data, decisions)
    skip = _skip(spec, data) or (str(skip_hint) if skip_hint else None)
    return InterpretedRow(
        kind=spec.kind, data=data, links=links, decisions=decisions, skip_reason=skip
    )


def merge_observation(row: InterpretedRow, spec: IngestionSpec, obs: dict) -> None:
    """ai_parse 列の AI 抽出結果を機械適用済みの行へマージする（宣言列限定の補完）。

    既存値は保持し、空フィールドのみ埋める。双方に値のある自由記述は「 / 」で連結する。
    マージ後に最小要件チェックを再評価する（AI 補完で氏名が埋まる場合等）。
    """
    extra = interpret_observation(spec, obs)
    for kind, v in extra.links.items():
        if kind not in row.links:
            row.links[kind] = v
        elif isinstance(row.links[kind], list) and isinstance(v, list):
            row.links[kind] = list(dict.fromkeys([*row.links[kind], *v]))
    for fld, v in extra.data.items():
        current = row.data.get(fld)
        if current in (None, ""):
            row.data[fld] = v
        elif isinstance(current, str) and isinstance(v, str) and v and v not in current:
            row.data[fld] = f"{current} / {v}"
    row.decisions.extend(extra.decisions)
    row.skip_reason = _skip(spec, row.data)
