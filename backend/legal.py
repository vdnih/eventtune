"""
法務文書のバージョン（同意判定の単一真実源）

利用規約・プライバシーポリシーの改定時にこの値を更新すると、同意済みユーザーにも
再同意ゲートが表示される。フロントは /api/users/me が返す current_terms_version と
ユーザーの terms_accepted_version を比較して判定する。

フロント側の表示用定数（frontend/lib/legal.ts CURRENT_TERMS_VERSION）と一致させること。
"""

CURRENT_TERMS_VERSION = "2026-07-03"
