/**
 * 汎用データ表示整形の特性化テスト。
 *
 * バックエンドが素の dict を返す設計のため、未知の型でも破綻しないことが
 * この層の不変条件。現在の表示仕様を暫定仕様として固定する。
 */
import { describe, expect, it } from "vitest";

import { formatCell, formatDetail, isComplex, pickEntityId, unionColumns } from "./format";

describe("formatCell", () => {
  it("null/undefined/空配列 は — にする", () => {
    expect(formatCell(null)).toBe("—");
    expect(formatCell(undefined)).toBe("—");
    expect(formatCell([])).toBe("—");
  });

  it("プリミティブは文字列化する", () => {
    expect(formatCell(true)).toBe("✓");
    expect(formatCell(false)).toBe("✗");
    expect(formatCell(42)).toBe("42");
    expect(formatCell("山田")).toBe("山田");
  });

  it("配列は join、オブジェクトは要約する", () => {
    expect(formatCell(["A", "B"])).toBe("A, B");
    expect(formatCell([{ a: 1 }])).toBe("{…}");
    expect(formatCell({ a: 1, b: 2 })).toBe("{ 2 項目 }");
  });
});

describe("formatDetail / isComplex", () => {
  it("オブジェクトは整形 JSON、プリミティブは文字列", () => {
    expect(formatDetail({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(formatDetail(true)).toBe("true");
    expect(formatDetail(null)).toBe("—");
  });

  it("isComplex はオブジェクト/配列のみ true", () => {
    expect(isComplex({})).toBe(true);
    expect(isComplex([])).toBe(true);
    expect(isComplex("str")).toBe(false);
    expect(isComplex(null)).toBe(false);
  });
});

describe("pickEntityId", () => {
  it("優先キー（person_id 等）を優先し、無ければ末尾 _id にフォールバックする", () => {
    expect(pickEntityId({ custom_id: "c1", person_id: "p1" })).toBe("p1");
    expect(pickEntityId({ custom_id: "c1" })).toBe("c1");
    expect(pickEntityId({ name: "山田" })).toBeNull();
  });
});

describe("unionColumns", () => {
  it("全行のキーの和集合を、ID列→名前列→出現順で返す", () => {
    const rows = [
      { memo: "x", name: "山田", person_id: "p1" },
      { email: "a@example.com", person_id: "p2" },
    ];
    expect(unionColumns(rows)).toEqual(["person_id", "name", "memo", "email"]);
  });
});
