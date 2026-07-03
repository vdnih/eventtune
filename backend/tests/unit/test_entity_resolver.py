"""EntityResolver（実在検索 find-or-create）の回帰テスト（ADR-011）。

安定ID（名前ハッシュ）を廃し、スペース内の実在エンティティを自然キーで検索する
find-or-create に一本化した。表記揺れの吸収（完全一致）・一意な包含一致・初出採番・
ファクトの (person,event,action) 重複防止を固定する。
"""

from agents.data_integration_agent import EntityResolver, _person_key


def test_exact_match_reuses_existing_uuid():
    res = EntityResolver("event", [("2025秋展示会", "event_aaa")], "event_")
    # 表記揺れ（全角数字・空白）でも同一マスタへ収束
    uid, created = res.resolve("２０２５ 秋 展示会")
    assert uid == "event_aaa"
    assert created is False


def test_first_occurrence_mints_new_uuid():
    res = EntityResolver("event", [], "event_")
    uid, created = res.resolve("新規イベント", display="新規イベント")
    assert created is True
    assert uid.startswith("event_")
    # 2 回目は同じ UUID（同一バッチ内でも重複しない）
    uid2, created2 = res.resolve("新規イベント")
    assert uid2 == uid
    assert created2 is False
    assert len(res.created) == 1


def test_unique_containment_fallback():
    res = EntityResolver("event", [("2025秋展示会 IT EXPO", "event_full")], "event_", fuzzy=True)
    # 一意な包含一致は既存へ寄せる
    uid, created = res.resolve("2025秋展示会 IT EXPO 東京")
    assert uid == "event_full"
    assert created is False


def test_no_fuzzy_for_persons_creates_distinct():
    res = EntityResolver("person", [("tanaka@acme.co.jp", "person_1")], "person_", fuzzy=False)
    uid, created = res.resolve("yamada@acme.co.jp", display="山田")
    assert created is True
    assert uid != "person_1"


def test_blank_name_resolves_to_none():
    res = EntityResolver("event", [], "event_")
    uid, created = res.resolve("")
    assert uid is None
    assert created is False


def test_person_key_prefers_email_then_name_company():
    assert _person_key("田中", "Tanaka@Acme.co.jp", "ACME") == _person_key(
        "別名", "tanaka@acme.co.jp", "他社"
    )
    # email 無しは name|company で識別
    k1 = _person_key("田中太郎", "", "ACME")
    k2 = _person_key("田中太郎", "", "別会社")
    assert k1 != k2


def test_attendance_idempotency_key():
    res = EntityResolver("attendance", [], "att_", fuzzy=False)
    aid1, c1 = res.resolve("person_1|event_1|参加")
    aid2, c2 = res.resolve("person_1|event_1|参加")
    assert aid1 == aid2 and c1 is True and c2 is False
    aid3, c3 = res.resolve("person_1|event_1|申し込み")
    assert aid3 != aid1 and c3 is True
