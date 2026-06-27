/**
 * 汎用データ表示のための値整形ユーティリティ。
 *
 * バックエンドは整形せず素の dict を返すため、型に応じて表示文字列に落とす。
 * オントロジーが変わっても破綻しないよう、未知の型は JSON 文字列にフォールバックする。
 */

/** セル1個ぶんの短い表示文字列。配列は join、オブジェクトは要約する。 */
export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "✓" : "✗";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    return value.map((v) => (typeof v === "object" ? "{…}" : String(v))).join(", ");
  }
  if (typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    return `{ ${keys.length} 項目 }`;
  }
  return String(value);
}

/** 詳細表示用の整形済み文字列。オブジェクト/配列は読みやすい JSON にする。 */
export function formatDetail(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/** オブジェクト/配列など、詳細では JSON ブロックで見せるべき値か。 */
export function isComplex(value: unknown): boolean {
  return value !== null && typeof value === "object";
}

/**
 * 行から「由来を追う」対象の entity_id を推定する。
 * 新オントロジー（person_id, account_id 等）を優先し、末尾 _id にフォールバックする。
 */
export function pickEntityId(row: Record<string, unknown>): string | null {
  const preferred = [
    "person_id",
    "account_id",
    "attendance_id",
    "interest_id",
    "deliverable_id",
    "job_id",
    "event_id",
    "segment_id",
    "run_id",
  ];
  for (const k of preferred) {
    const v = row[k];
    if (typeof v === "string" && v) return v;
  }
  for (const [k, v] of Object.entries(row)) {
    if (k.endsWith("_id") && typeof v === "string" && v) return v;
  }
  return null;
}

/** 全行のキーの和集合を、出現順を保ちつつ列順として返す。 */
export function unionColumns(rows: Record<string, unknown>[]): string[] {
  const cols: string[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key);
        cols.push(key);
      }
    }
  }
  return cols;
}
