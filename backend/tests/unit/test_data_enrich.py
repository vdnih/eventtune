"""data ルーターのファクト→マスタ表示名エンリッチの単体テスト。

Firestore は物理 JOIN しないため、ファクト行にマスタ表示名を付与するのは Python 側の
決定論ロジック。ここではその付与が正しく行われることを、最小の Fake space で固定する。
"""

from routers.data import _enrich_rows


class _Snap:
    def __init__(self, doc_id: str, data: dict):
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict:
        return self._data


class _Col:
    def __init__(self, docs: dict[str, dict]):
        self._docs = docs

    def stream(self):
        for doc_id, data in self._docs.items():
            yield _Snap(doc_id, data)


class _FakeSpace:
    """space.col(name).stream() のみを提供する最小フェイク。"""

    def __init__(self, collections: dict[str, dict[str, dict]]):
        self._collections = collections

    def col(self, name: str) -> _Col:
        return _Col(self._collections.get(name, {}))


def test_enrich_event_attendances_adds_person_and_event_names():
    space = _FakeSpace(
        {
            "persons": {"p1": {"name": "山田太郎"}, "p2": {"name": "佐藤花子"}},
            "events": {"e1": {"name": "SaaS Conf 2026"}},
        }
    )
    rows = [
        {"attendance_id": "a1", "person_id": "p1", "event_id": "e1"},
        {"attendance_id": "a2", "person_id": "p2", "event_id": "e1"},
    ]
    out = _enrich_rows(space, "event_attendances", rows)
    assert out[0]["person_name"] == "山田太郎"
    assert out[0]["event_name"] == "SaaS Conf 2026"
    assert out[1]["person_name"] == "佐藤花子"


def test_enrich_skips_unknown_fk_and_unregistered_view():
    space = _FakeSpace({"accounts": {"acc1": {"account_name": "ACME"}}})
    # 未登録ビューは素通し
    rows = [{"foo": "bar"}]
    assert _enrich_rows(space, "segments", rows) == [{"foo": "bar"}]
    # FK が辞書に無い行は名前が付与されない（KeyError にならない）
    rows2 = [{"person_id": "p1", "account_id": "missing"}]
    out = _enrich_rows(space, "persons", rows2)
    assert "account_name" not in out[0]
