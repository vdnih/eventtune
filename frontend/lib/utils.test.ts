import { describe, expect, it } from "vitest";

import { cn, formatDate } from "./utils";

describe("cn", () => {
  it("クラス名を結合し、Tailwind の競合は後勝ちでマージする", () => {
    expect(cn("px-2", "py-1")).toBe("px-2 py-1");
    expect(cn("px-2", "px-4")).toBe("px-4");
    expect(cn("text-sm", false && "hidden", undefined)).toBe("text-sm");
  });
});

describe("formatDate", () => {
  it("日付を日本語ロケール（YYYY/MM/DD）で整形する", () => {
    expect(formatDate("2026-07-03")).toBe("2026/07/03");
    expect(formatDate(new Date(2026, 0, 5))).toBe("2026/01/05");
  });
});
