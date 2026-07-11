"""
SpaceData — Firestore から全エンティティをロードして型付き Pydantic モデルへ変換する。

ADR-008 フラットスキーマ対応: 全エンティティがトップレベルコレクションになったため、
旧スキーマの 3 重ループ traversal は不要。各コレクションを stream() 1 回で取得できる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ontology import (
    Account,
    Content,
    CostItem,
    Event,
    EventAttendance,
    Person,
    Product,
    ProductInterest,
    Segment,
)
from space import SpaceContext


@dataclass
class SpaceData:
    events: list[Event] = field(default_factory=list)
    persons: list[Person] = field(default_factory=list)
    accounts: list[Account] = field(default_factory=list)
    event_attendances: list[EventAttendance] = field(default_factory=list)
    product_interests: list[ProductInterest] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    contents: list[Content] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    cost_items: list[CostItem] = field(default_factory=list)


def load_space_data(space: SpaceContext) -> SpaceData:
    """スペースの全エンティティを Firestore から読み込み SpaceData として返す。

    Pydantic で厳格にバリデーションするため、スキーマ不整合がある行は警告ログを出して
    スキップする（例外は上位に伝播させない）。
    """
    import logging

    logger = logging.getLogger(__name__)

    def _stream(col_name: str, model_class):
        results = []
        for doc in space.col(col_name).stream():
            data = doc.to_dict()
            if not data:
                continue
            try:
                results.append(model_class.model_validate(data))
            except Exception as e:
                logger.warning("skip invalid %s doc=%s: %s", col_name, doc.id, e)
        return results

    return SpaceData(
        events=_stream("events", Event),
        persons=_stream("persons", Person),
        accounts=_stream("accounts", Account),
        event_attendances=_stream("event_attendances", EventAttendance),
        product_interests=_stream("product_interests", ProductInterest),
        products=_stream("products", Product),
        contents=_stream("contents", Content),
        segments=_stream("segments", Segment),
        cost_items=_stream("cost_items", CostItem),
    )
