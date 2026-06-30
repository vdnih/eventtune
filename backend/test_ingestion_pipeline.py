"""取り込み多段パイプライン（conform→bind→derive）のオフライン結合テスト（ADR-013）。

実 Firestore/AI を使わず、Fake な ScopedClient と stub した appeal 生成で、依存順の多段が
狙いどおり収束することを固定する:
- event_attendances.event_id が、投入順・表記揺れに依らず単一の events ドキュメントへ収束。
- contents.linked_event_id がイベント名照合で解決（従来空だった穴の解消）。
- 同一 email の観測が 1 person に統合（重複なし）。
- person.appeal_summary が導出ステージで付与される。
"""

import asyncio

import semantic_search
from agents.ontology_mapper import InterpretedRecord, MapResult, PersonObservation
import agents.data_integration_agent as agent
from agents.data_integration_agent import (
    EntityResolver,
    _FileInterpretation,
    _bind_facts,
    _conform_masters,
    _derive_person_appeal,
    _load_existing,
    _load_existing_persons,
)


# ── Fake Firestore（ScopedClient 互換の最小実装）─────────────────────────────────

class _Snap:
    def __init__(self, path, data):
        self._d = data
        self.id = path.split("/")[-1]
        self.exists = data is not None

    def to_dict(self):
        return self._d


class _Doc:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def set(self, data, merge=False):
        if merge and self._path in self._store:
            self._store[self._path].update(data)
        else:
            self._store[self._path] = dict(data)

    def get(self):
        return _Snap(self._path, self._store.get(self._path))


class _Query:
    def __init__(self, docs):
        self._docs = docs

    def where(self, field, _op, val):
        return _Query([d for d in self._docs if (d._d or {}).get(field) == val])

    def get(self):
        return self._docs


class _Collection:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def _docs(self):
        out = []
        prefix = self._name + "/"
        for path, data in self._store.items():
            if path.startswith(prefix) and "/" not in path[len(prefix):]:
                out.append(_Snap(path, data))
        return out

    def get(self):
        return self._docs()

    def where(self, field, _op, val):
        return _Query([d for d in self._docs() if (d._d or {}).get(field) == val])

    def document(self, doc_id):
        return _Doc(self._store, f"{self._name}/{doc_id}")


class _FakeDB:
    def __init__(self):
        self.store: dict[str, dict] = {}

    def collection(self, name):
        return _Collection(self.store, name)

    def document(self, path):
        return _Doc(self.store, path)


def _interp(map_result) -> _FileInterpretation:
    return _FileInterpretation(filename="f", job_id="job_f", map_result=map_result)


def _run(db, interps, default_event=""):
    resolvers = {
        "events": EntityResolver("event", _load_existing(db, "events", "name", "event_id"), "event_"),
        "accounts": EntityResolver(
            "account", _load_existing(db, "accounts", "account_name", "account_id"), "account_"),
        "products": EntityResolver(
            "product", _load_existing(db, "products", "product_name", "product_id"), "product_"),
        "contents": EntityResolver(
            "content", _load_existing(db, "contents", "content_name", "content_id"), "content_"),
        "persons": EntityResolver("person", _load_existing_persons(db), "person_", fuzzy=False),
    }

    async def go():
        batch_event_ids = await _conform_masters(db, None, interps, default_event, resolvers)
        touched = await _bind_facts(
            db, None, interps, resolvers, default_event, "job_x", batch_event_ids=batch_event_ids)
        await _derive_person_appeal(db, None, touched)
        return touched

    return asyncio.run(go())


def _docs(db, collection):
    prefix = collection + "/"
    return [v for k, v in db.store.items()
            if k.startswith(prefix) and "/" not in k[len(prefix):]]


def _event_record(name: str) -> InterpretedRecord:
    """テスト用: イベントの InterpretedRecord を直接生成する。"""
    from ontology import EventStatus, EventType
    return InterpretedRecord(
        kind="events",
        name=name,
        payload={"name": name, "event_type": EventType.TRADE_SHOW, "status": EventStatus.COMPLETED,
                 "venue": "", "event_date": "", "event_date_end": "", "booth_number": None,
                 "total_budget": 0.0, "target_contact_count": 0, "description": "", "created_at": ""},
    )


def _content_record(name: str, event_name: str) -> InterpretedRecord:
    """テスト用: コンテンツの InterpretedRecord を直接生成する。"""
    from ontology import ContentType
    return InterpretedRecord(
        kind="contents",
        name=name,
        payload={"content_name": name, "content_type": ContentType.WHITE_PAPER, "url": "http://x",
                 "description": ""},
        links={"event": event_name},
    )


def test_pipeline_converges_links_and_dedups(monkeypatch):
    async def _fake_build_appeal(kind, payload, space=None):
        return f"summary:{kind}", [0.1, 0.2, 0.3]

    monkeypatch.setattr(semantic_search, "build_appeal", _fake_build_appeal)
    monkeypatch.setattr(agent.semantic_search, "build_appeal", _fake_build_appeal)

    # ファイル1: 参加者（イベント名は表記揺れ「２０２５秋 展示会」）。同一 email を2行。
    persons_result = MapResult(
        person_observations=[
            PersonObservation(
                name="田中太郎", email="t@a.com", company_name="ACME",
                event_link_name="２０２５秋 展示会", product_link_names=["プロダクトA"],
            ),
            PersonObservation(
                name="田中太郎", email="t@a.com", company_name="ACME",
                event_link_name="２０２５秋 展示会", product_link_names=["プロダクトA"],
            ),
        ]
    )

    # ファイル2: イベントマスタ（正規表記「2025秋展示会」）
    events_result = MapResult(records=[_event_record("2025秋展示会")])

    # ファイル3: 素材（別の表記「2025 秋 展示会」でイベントへリンク）
    contents_result = MapResult(records=[_content_record("導入事例A", "2025 秋 展示会")])

    db = _FakeDB()
    interps = [_interp(persons_result), _interp(events_result), _interp(contents_result)]
    touched = _run(db, interps)

    # イベントは表記揺れに依らず 1 つへ収束
    events = _docs(db, "events")
    assert len(events) == 1
    event_id = events[0]["event_id"]
    assert events[0]["name"] == "2025秋展示会"

    # 参加ファクトが実在イベントへ JOIN 可能
    atts = _docs(db, "event_attendances")
    assert len(atts) == 1  # 同一 (person,event,action) は冪等
    assert atts[0]["event_id"] == event_id
    assert atts[0]["person_id"] in touched

    # contents→event リンクが解決
    contents = _docs(db, "contents")
    assert len(contents) == 1
    assert contents[0]["linked_event_id"] == event_id

    # 同一 email は 1 person に統合
    persons = _docs(db, "persons")
    assert len(persons) == 1
    # 製品関心も解決
    assert len(_docs(db, "product_interests")) == 1
    assert len(_docs(db, "products")) == 1

    # 導出ステージで appeal_summary が付与される
    assert persons[0]["appeal_summary"] == "summary:person"
    assert persons[0]["appeal_vector"]


def test_solo_event_fallback_binds_linkless_persons(monkeypatch):
    """参加者ファイルにイベント列が無くても、バッチに単一イベントがあれば紐付く。"""
    async def _fake_build_appeal(kind, payload, space=None):
        return f"summary:{kind}", [0.1]

    monkeypatch.setattr(agent.semantic_search, "build_appeal", _fake_build_appeal)

    # 参加者ファイル（イベントリンク無し）
    persons_result = MapResult(
        person_observations=[
            PersonObservation(name="田中太郎", email="a@a.com", company_name="ACME"),
            PersonObservation(name="鈴木花子", email="b@b.com", company_name="ベータ"),
        ]
    )
    assert all(o.event_link_name == "" for o in persons_result.person_observations)

    # イベント概要ファイル（同一バッチに 1 イベント）
    events_result = MapResult(records=[_event_record("スマート工場EXPO 2025秋")])

    db = _FakeDB()
    _run(db, [_interp(persons_result), _interp(events_result)])

    events = _docs(db, "events")
    assert len(events) == 1
    event_id = events[0]["event_id"]

    atts = _docs(db, "event_attendances")
    assert len(atts) == 2  # 両名がフォールバックでイベントへ紐付く
    assert all(a["event_id"] == event_id for a in atts)


def test_no_event_in_batch_creates_no_attendance(monkeypatch):
    """イベントが一切無いバッチ（参加者のみ）はフォールバックせず参加 0 件。"""
    async def _fake_build_appeal(kind, payload, space=None):
        return "", []

    monkeypatch.setattr(agent.semantic_search, "build_appeal", _fake_build_appeal)

    persons_result = MapResult(
        person_observations=[
            PersonObservation(name="田中太郎", email="a@a.com"),
        ]
    )

    db = _FakeDB()
    _run(db, [_interp(persons_result)])

    assert len(_docs(db, "persons")) == 1
    assert len(_docs(db, "event_attendances")) == 0
