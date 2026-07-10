import { describe, expect, it } from "vitest";

import { aggregateColumn, summarize } from "./aggregate";

const rows = [
  { amount: 100, category: "会場費", ok: true },
  { amount: 300, category: "会場費", ok: false },
  { amount: 200, category: "集客", ok: true },
  { amount: null, category: "集客" },
];

describe("aggregateColumn", () => {
  it("数値列は件数・合計・平均・最小・最大を出す（null除外）", () => {
    const s = aggregateColumn(rows, "amount");
    expect(s).toMatchObject({
      kind: "number",
      count: 3,
      sum: 600,
      avg: 200,
      min: 100,
      max: 300,
    });
  });

  it("文字列列は distinct と上位分布を出す", () => {
    const s = aggregateColumn(rows, "category");
    expect(s.kind).toBe("category");
    if (s.kind === "category") {
      expect(s.distinct).toBe(2);
      expect(s.top[0]).toEqual({ value: "会場費", count: 2 });
    }
  });

  it("真偽列は true/false 件数を出す", () => {
    const s = aggregateColumn(rows, "ok");
    expect(s).toMatchObject({ kind: "boolean", count: 3, trueCount: 2, falseCount: 1 });
  });
});

describe("summarize", () => {
  it("指定列すべてを集計する", () => {
    const out = summarize(rows, ["amount", "category"]);
    expect(out.map((s) => s.kind)).toEqual(["number", "category"]);
  });
});
