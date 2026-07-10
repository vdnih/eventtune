"""ingestion.readers の単体テスト（ファイル形式判定 + Read ステージの抽出ロジック）。"""

import io

import docx as docx_lib
import pandas as pd
import pytest

from ingestion import readers


def _make_docx(
    paragraphs: tuple[str, ...] = (), table_rows: list[list[str]] | None = None
) -> bytes:
    document = docx_lib.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    if table_rows:
        table = document.add_table(rows=0, cols=len(table_rows[0]))
        for row in table_rows:
            cells = table.add_row().cells
            for i, value in enumerate(row):
                cells[i].text = value
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _make_xlsx(rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    return buf.getvalue()


def test_is_supported_accepts_docx():
    assert readers.is_supported("overview.docx")
    assert readers.is_docx("overview.docx")
    assert not readers.is_tabular("overview.docx")


def test_ensure_supported_rejects_legacy_doc_and_pdf():
    for name in ("legacy.doc", "report.pdf"):
        with pytest.raises(readers.UnsupportedFileError):
            readers.ensure_supported(name)


def test_read_docx_extracts_paragraphs_and_tables():
    content = _make_docx(
        paragraphs=("概要テキスト",),
        table_rows=[["日程", "会場"], ["10/1", "東京"]],
    )
    text = readers.read_docx(content)
    assert "概要テキスト" in text
    assert "日程 | 会場" in text
    assert "10/1 | 東京" in text


def test_read_blocks_docx_returns_single_text_block():
    content = _make_docx(paragraphs=("概要",))
    blocks = readers.read_blocks("overview.docx", content)
    assert len(blocks) == 1
    assert blocks[0].row_no == 0
    assert "概要" in blocks[0].raw["text"]
    assert blocks[0].read_error == ""


def test_read_blocks_corrupt_docx_yields_read_error():
    blocks = readers.read_blocks("broken.docx", b"not a real docx file")
    assert len(blocks) == 1
    assert blocks[0].read_error.startswith("読み込みエラー")


def test_read_blocks_xlsx_returns_row_blocks():
    content = _make_xlsx([{"会社名": "株式会社A", "お名前": "山田様"}, {"会社名": "株式会社B", "お名前": "鈴木様"}])
    blocks = readers.read_blocks("list.xlsx", content)
    assert len(blocks) == 2
    assert blocks[0].read_error == ""
    assert blocks[0].raw == {"会社名": "株式会社A", "お名前": "山田様"}
    assert blocks[1].raw == {"会社名": "株式会社B", "お名前": "鈴木様"}


def test_read_blocks_corrupt_xlsx_yields_read_error():
    blocks = readers.read_blocks("broken.xlsx", b"not a real xlsx file")
    assert len(blocks) == 1
    assert blocks[0].read_error.startswith("読み込みエラー")
