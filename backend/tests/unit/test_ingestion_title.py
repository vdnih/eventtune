"""_ingestion_title（取り込みチャットのスレッドタイトル生成）の分岐テスト。

イベントに関する取り込みは既定イベント名、イベントに紐づかないデータ（コンテンツリスト等）は
その種別名がタイトルになる。plan が無い場合は従来通りファイル名にフォールバックする。
"""

from ontology import BatchPlan, DefaultEventPlan, FilePlan, TargetPlan
from routers.integration import _ingestion_title


def _target_plan(entity_type: str) -> FilePlan:
    return FilePlan(filename=f"{entity_type}.csv", targets=[TargetPlan(entity_type=entity_type)])


def test_default_event_name_wins_over_dataset_kind():
    plan = BatchPlan(
        default_event=DefaultEventPlan(name="展示会X", is_existing=False),
        files=[_target_plan("event_attendances")],
    )
    assert _ingestion_title(["attendees.csv"], plan) == "展示会X"


def test_single_dataset_kind_without_event():
    plan = BatchPlan(files=[_target_plan("contents")])
    assert _ingestion_title(["contents.csv"], plan) == "コンテンツリストの取り込み"


def test_two_dataset_kinds_without_event():
    plan = BatchPlan(files=[_target_plan("contents"), _target_plan("products")])
    assert (
        _ingestion_title(["a.csv", "b.csv"], plan) == "コンテンツリスト・プロダクトリストの取り込み"
    )


def test_three_or_more_dataset_kinds_without_event():
    plan = BatchPlan(
        files=[_target_plan("contents"), _target_plan("products"), _target_plan("accounts")]
    )
    assert _ingestion_title(["a.csv", "b.csv", "c.csv"], plan) == "コンテンツリスト他2種の取り込み"


def test_falls_back_to_filename_when_plan_missing():
    assert _ingestion_title(["report.xlsx"]) == "取り込み: report.xlsx"


def test_falls_back_to_filename_when_plan_has_no_targets():
    plan = BatchPlan(files=[FilePlan(filename="empty.csv")])
    assert _ingestion_title(["empty.csv"], plan) == "取り込み: empty.csv"


def test_falls_back_to_default_label_when_no_files():
    assert _ingestion_title([]) == "データ取り込み"
