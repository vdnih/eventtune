"""
ingestion — 取り込みのスペック駆動基盤（ADR-015 / docs/INGESTION_MAPPING.md）

- specs:     IngestionSpec レジストリ（データセット追加 = ontology.py のモデル + ここに1エントリ）
- engine:    解釈エンジン（承認済み変換仕様の機械適用。純粋・I/O なし）
- prompts:   レジストリからプロンプトを描画する単一レンダラー
- readers:   ファイル → 観測ブロック（対応形式の判定と読み込み）
- normalize: 照合キー正規化・値の normalizer（登録制の純関数）
"""
