/**
 * 決定論的なテーブル集計。
 *
 * バックエンドは素の dict を返すため、列の型を値から推定して数値集計・分布を出す。
 * AI 生成サマリ（TableSummary）とは独立に、常時この集計をフロントで計算する。
 */

export type ColumnStats =
  | {
      key: string;
      kind: "number";
      count: number;
      sum: number;
      avg: number;
      min: number;
      max: number;
    }
  | {
      key: string;
      kind: "boolean";
      count: number;
      trueCount: number;
      falseCount: number;
    }
  | {
      key: string;
      kind: "category";
      count: number;
      distinct: number;
      top: { value: string; count: number }[];
    }
  | { key: string; kind: "other"; count: number };

const TOP_N = 5;

/** 1列ぶんの集計。非 null 値の型から数値/真偽/カテゴリ/その他を判定する。 */
export function aggregateColumn(rows: Record<string, unknown>[], key: string): ColumnStats {
  const values = rows.map((r) => r[key]).filter((v) => v !== null && v !== undefined);
  const count = values.length;

  if (count > 0 && values.every((v) => typeof v === "number")) {
    const nums = values as number[];
    const sum = nums.reduce((a, b) => a + b, 0);
    return {
      key,
      kind: "number",
      count,
      sum,
      avg: sum / count,
      min: Math.min(...nums),
      max: Math.max(...nums),
    };
  }

  if (count > 0 && values.every((v) => typeof v === "boolean")) {
    const trueCount = (values as boolean[]).filter(Boolean).length;
    return { key, kind: "boolean", count, trueCount, falseCount: count - trueCount };
  }

  if (count > 0 && values.every((v) => typeof v === "string")) {
    const freq = new Map<string, number>();
    for (const v of values as string[]) freq.set(v, (freq.get(v) ?? 0) + 1);
    const top = [...freq.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, TOP_N)
      .map(([value, c]) => ({ value, count: c }));
    return { key, kind: "category", count, distinct: freq.size, top };
  }

  return { key, kind: "other", count };
}

/** 指定列すべての集計を返す。 */
export function summarize(
  rows: Record<string, unknown>[],
  columns: string[],
): ColumnStats[] {
  return columns.map((key) => aggregateColumn(rows, key));
}
