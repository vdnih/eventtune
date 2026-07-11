"""GET /api/marketing/segments/{segment_id}/patterns の疎通確認。

generate_patterns はツールの戻り値に本文を含めない（件名のみ）ため、文面チェックUIは
このエンドポイントで segments/{segment_id}/patterns から生成済みパターン本文を取得する。
"""

import pytest

pytestmark = pytest.mark.integration


def _seed_pattern(db, space_id, segment_id, bucket, output_format="EMAIL", subject="件名"):
    pattern_id = f"{bucket}__{output_format}"
    db.document(f"spaces/{space_id}/segments/{segment_id}/patterns/{pattern_id}").set(
        {
            "pattern_id": pattern_id,
            "segment_id": segment_id,
            "bucket": bucket,
            "format": output_format,
            "subject": subject,
            "blocks": [
                {
                    "block_type": "greeting",
                    "block_text": "{name} 様",
                    "reason_for_inclusion": "冒頭挨拶のため",
                    "associated_asset_ids": [],
                }
            ],
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )


def test_returns_all_patterns_for_segment(make_client, seeded_space, db):
    _seed_pattern(db, seeded_space, "seg_1", "資格法令対応×高熱量")
    _seed_pattern(db, seeded_space, "seg_1", "多能工最適化×高熱量")

    client = make_client(uid="uid_owner")
    res = client.get("/api/marketing/segments/seg_1/patterns", headers={"X-Space-Id": seeded_space})

    assert res.status_code == 200
    data = res.json()
    assert data["segment_id"] == "seg_1"
    assert data["count"] == 2
    buckets = sorted(p["bucket"] for p in data["patterns"])
    assert buckets == ["多能工最適化×高熱量", "資格法令対応×高熱量"]
    assert data["patterns"][0]["blocks"][0]["block_text"] == "{name} 様"


def test_filters_by_output_format(make_client, seeded_space, db):
    _seed_pattern(db, seeded_space, "seg_2", "バケットA", output_format="EMAIL")
    _seed_pattern(db, seeded_space, "seg_2", "バケットA", output_format="TALK_SCRIPT")

    client = make_client(uid="uid_owner")
    res = client.get(
        "/api/marketing/segments/seg_2/patterns",
        params={"output_format": "TALK_SCRIPT"},
        headers={"X-Space-Id": seeded_space},
    )

    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["patterns"][0]["format"] == "TALK_SCRIPT"


def test_returns_empty_for_unknown_segment(make_client, seeded_space, db):
    client = make_client(uid="uid_owner")
    res = client.get(
        "/api/marketing/segments/seg_missing/patterns", headers={"X-Space-Id": seeded_space}
    )

    assert res.status_code == 200
    assert res.json() == {"segment_id": "seg_missing", "patterns": [], "count": 0}
