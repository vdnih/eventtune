/**
 * 汎用データ表示整形の特性化テスト。
 *
 * バックエンドが素の dict を返す設計のため、未知の型でも破綻しないことが
 * この層の不変条件。現在の表示仕様を暫定仕様として固定する。
 */
import { describe, expect, it } from "vitest";

import {
  classifyColumns,
  formatCell,
  formatDetail,
  formatTimestamp,
  isComplex,
  isMetadataColumn,
  unionColumns,
} from "./format";

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

describe("unionColumns", () => {
  it("全行のキーの和集合を、ID列→名前列→出現順で返す", () => {
    const rows = [
      { memo: "x", name: "山田", person_id: "p1" },
      { email: "a@example.com", person_id: "p2" },
    ];
    expect(unionColumns(rows)).toEqual(["person_id", "name", "memo", "email"]);
  });
});

describe("formatTimestamp", () => {
  it("ISO 日時は 年月日時分 に整形する", () => {
    // ローカルタイムで検証（TZ 依存を避け getHours 等で比較）。
    const out = formatTimestamp("2026-07-10T12:34:56.789Z");
    expect(out).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  });
  it("日付のみ・非日時は null（＝素通し）", () => {
    expect(formatTimestamp("2026-07-10")).toBeNull();
    expect(formatTimestamp("参加")).toBeNull();
    expect(formatTimestamp(42)).toBeNull();
  });
});

describe("ベクトル・日時の表示", () => {
  it("*_vector は次元数に要約する", () => {
    const vec = Array.from({ length: 768 }, () => 0.1);
    expect(formatCell(vec, "appeal_vector")).toBe("768次元ベクトル");
    expect(formatDetail(vec, "appeal_vector")).toContain("768次元ベクトル");
    expect(isComplex(vec, "appeal_vector")).toBe(false);
  });
  it("created_at は年月日時分で表示する", () => {
    expect(formatCell("2026-07-10T12:34:56Z", "created_at")).toMatch(
      /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/,
    );
  });
});

describe("isMetadataColumn", () => {
  it("ID・space_id・ベクトルは true、表示名・時刻は false", () => {
    expect(isMetadataColumn("person_id")).toBe(true);
    expect(isMetadataColumn("id")).toBe(true);
    expect(isMetadataColumn("space_id")).toBe(true);
    expect(isMetadataColumn("appeal_vector")).toBe(true);
    expect(isMetadataColumn("person_name")).toBe(false);
    expect(isMetadataColumn("created_at")).toBe(false);
  });
});

describe("classifyColumns", () => {
  it("メタデータを分離し、表示名を primary の先頭に寄せる", () => {
    const rows = [
      { person_id: "p1", memo: "x", person_name: "山田", appeal_vector: [0.1], space_id: "s1" },
    ];
    const { primary, metadata } = classifyColumns(rows);
    expect(primary).toEqual(["person_name", "memo"]);
    expect(metadata).toEqual(["person_id", "appeal_vector", "space_id"]);
  });
});
