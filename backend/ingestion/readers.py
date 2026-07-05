"""
readers — ファイル → 観測ブロック（Read ステージの読み込み部。純粋・Firestore 非依存）

対応形式は CSV / Excel / テキストのみ。PDF 等の未対応形式は UnsupportedFileError を
送出し、ルーターが 400 に変換する（読めない文字化けを AI に渡さない。ADR-015 決定7）。
"""

import io
from dataclasses import dataclass

import pandas as pd

SUPPORTED_TABULAR = (".csv", ".xlsx", ".xls")
SUPPORTED_TEXT = (".txt",)


class UnsupportedFileError(ValueError):
    """未対応のファイル形式（PDF 等）。ルーターが 400 に変換する。"""


def is_tabular(filename: str) -> bool:
    return filename.lower().endswith(SUPPORTED_TABULAR)


def is_supported(filename: str) -> bool:
    return filename.lower().endswith(SUPPORTED_TABULAR + SUPPORTED_TEXT)


def ensure_supported(filename: str) -> None:
    if not is_supported(filename):
        raise UnsupportedFileError(
            f"未対応のファイル形式です: {filename}（対応形式: CSV / Excel / テキスト）"
        )


def read_tabular(filename: str, content: bytes) -> tuple[list[str], list[dict]]:
    """表形式ファイルを (ヘッダー, 行データ) で返す。値はすべて文字列。"""
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", dtype=str)
    else:
        df = pd.read_excel(io.BytesIO(content), dtype=str)
    df = df.fillna("")
    return list(df.columns), df.to_dict(orient="records")


def read_text(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


@dataclass
class SourceBlock:
    """観測ブロック（source_records に着地する単位）。表=1行、文書=1件。"""

    row_no: int
    raw: dict  # {元列: 値}（文書は {"text": 全文}）
    read_error: str = ""  # 読み込み失敗の理由（非空なら skipped として着地）


def read_blocks(filename: str, content: bytes) -> list[SourceBlock]:
    """ファイルを観測ブロック列に変換する。未対応形式は UnsupportedFileError。"""
    ensure_supported(filename)
    if is_tabular(filename):
        try:
            _, rows = read_tabular(filename, content)
        except Exception as e:  # 壊れた表ファイル: 1ブロックの読み込みエラーとして着地
            return [SourceBlock(row_no=0, raw={}, read_error=f"読み込みエラー: {e}")]
        return [SourceBlock(row_no=i, raw=row) for i, row in enumerate(rows)]
    return [SourceBlock(row_no=0, raw={"text": read_text(content)})]
