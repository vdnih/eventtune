"""
SpaceContext — 中央データアクセス層（Context-Bound Data Access）

マルチテナント分離の背骨。テナント（スペース）分離を「規律（毎回フィルタを書き忘れない）」
ではなく「構造（スコープ外の参照を手にする手段が存在しない）」で担保する。

設計判断:
- Context Object パターン: 「誰が・どのスペースで・どの権限で」を単一の不変オブジェクトに集約。
- ケイパビリティとしてのデータ参照: space_id という座標ではなく、「自スペースにしか到達できない
  スコープ済み参照を返す能力」を運ぶ。業務コードは col()/doc() しか触れず、生の
  firestore.client() を業務ロジックから撤廃する（唯一のサンクション済み入口）。
- トラスト境界での一度きりの束縛: SpaceContext は dependencies.get_space_context で
  トークン検証＋membership照合を済ませた後にのみ生成される。

詳細は docs/PHILOSOPHY_AND_NAMING.md「Context-Bound Data Access」を参照。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ScopedClient:
    """firestore.client() のスコープ済みファサード。

    collection(path) / document(path) を必ず spaces/{space_id}/ で前置する。
    多数のパスを文字列で組み立てるコード（データ統合パイプライン・AIツール群）に対し、
    生クライアントの代わりにこれを渡すことで、それらのコードを書き換えずに
    「自スペースにしか到達できない」ケイパビリティへ縮約する。

    AI境界での最小権限: AIツールはこのファサードを closure で掴むため、space_id を
    パラメータとして受け取らず・表現できない（他スペースを名指しする経路が存在しない）。
    """

    def __init__(self, db: Any, space_id: str):
        self._db = db
        self._prefix = f"spaces/{space_id}"

    def collection(self, path: str):
        return self._db.collection(f"{self._prefix}/{path}")

    def document(self, path: str):
        return self._db.document(f"{self._prefix}/{path}")

    def batch(self):
        return self._db.batch()


@dataclass(frozen=True)
class SpaceContext:
    """検証済みのテナント実行文脈。get_space_context でのみ生成される。

    Attributes:
        space_id: 操作対象スペースID（membership照合済み）
        uid:      Firebase 署名検証済みのユーザーID
        role:     "owner" | "member"（membership doc 由来。クライアント申告は使わない）
        db:       firestore.client()。col()/doc() を介してのみ使うこと。
    """

    space_id: str
    uid: str
    role: str
    db: Any

    def col(self, name: str):
        """スペース配下のコレクション参照を返す。

        例: col("events") -> spaces/{space_id}/events
            col(f"events/{eid}/kpi") -> spaces/{space_id}/events/{eid}/kpi
        """
        return self.db.collection(f"spaces/{self.space_id}/{name}")

    def doc(self, path: str):
        """スペース配下のドキュメント参照を返す。

        例: doc(f"events/{eid}") -> spaces/{space_id}/events/{eid}
        """
        return self.db.document(f"spaces/{self.space_id}/{path}")

    def scoped_db(self) -> ScopedClient:
        """生クライアントの代わりに渡せるスコープ済みファサードを返す。

        データ統合パイプラインや AI ツール群（多数のパスを文字列で組み立てるコード）に
        生 db の代わりに渡す。詳細は ScopedClient を参照。
        """
        return ScopedClient(self.db, self.space_id)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"
