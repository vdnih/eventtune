/**
 * 汎用データ表示のための値整形ユーティリティ。
 *
 * バックエンドは（マスタ表示名の付与を除き）素の dict を返すため、型に応じて表示文字列に
 * 落とす。オントロジーが変わっても破綻しないよう、未知の型は JSON 文字列にフォールバックする。
 */

/** 埋め込みベクトル（*_vector、または数値の大きな配列）か。 */
function isVectorValue(value: unknown, key?: string): value is number[] {
  if (key && key.endsWith("_vector")) return Array.isArray(value);
  return (
    Array.isArray(value) &&
    value.length > 16 &&
    value.every((v) => typeof v === "number")
  );
}

const pad2 = (n: number): string => String(n).padStart(2, "0");

/**
 * ISO 8601 の日時文字列を `YYYY-MM-DD HH:mm`（ローカル時刻、秒・ミリ秒・TZ を省略）に整形する。
 * 日時に見えない値・日付のみの値は null を返す（呼び出し側でそのまま表示）。
 */
export function formatTimestamp(value: unknown): string | null {
  if (typeof value !== "string") return null;
  // 日付＋時刻（T または空白区切り）のみ対象。日付のみ（YYYY-MM-DD）は素通し。
  if (!/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(value)) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return (
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ` +
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
  );
}

/** セル1個ぶんの短い表示文字列。ベクトルは要約、日時は年月日時分、配列は join。 */
export function formatCell(value: unknown, key?: string): string {
  if (value === null || value === undefined) return "—";
  if (isVectorValue(value, key)) return `${value.length}次元ベクトル`;
  if (typeof value === "boolean") return value ? "✓" : "✗";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return formatTimestamp(value) ?? value;
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

/** 詳細表示用の整形済み文字列。ベクトルは次元数＋先頭数件、日時は年月日時分、他はJSON/文字列。 */
export function formatDetail(value: unknown, key?: string): string {
  if (value === null || value === undefined) return "—";
  if (isVectorValue(value, key)) {
    const head = value
      .slice(0, 4)
      .map((v) => (typeof v === "number" ? v.toFixed(3) : String(v)))
      .join(", ");
    return `${value.length}次元ベクトル（先頭: ${head} …）`;
  }
  if (typeof value === "string") return formatTimestamp(value) ?? value;
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/** オブジェクト/配列など、詳細では JSON ブロックで見せるべき値か（ベクトルは除く）。 */
export function isComplex(value: unknown, key?: string): boolean {
  if (isVectorValue(value, key)) return false;
  return value !== null && typeof value === "object";
}

/**
 * 表には既定で出さず、詳細の「メタデータ」節に回す列か。
 * ID（主キー・FK）・space_id・埋め込みベクトルが対象。表示名（*_name）は対象外。
 */
export function isMetadataColumn(key: string): boolean {
  return (
    key === "id" ||
    key === "space_id" ||
    key.endsWith("_id") ||
    key.endsWith("_vector")
  );
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

/**
 * 全行のキーの和集合を列順として返す。
 * 全ビューで表示を統一するため、ID列を先頭・名前列を2番目に固定し、残りは出現順を保つ。
 * モデル固有知識は持たず命名規則で判定する（ID=最初の `*_id`/`id`、名前=`name`→最初の `*_name`）。
 */
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
  const idCol = cols.find((c) => c === "id" || c.endsWith("_id"));
  const nameCol = cols.find((c) => c === "name") ?? cols.find((c) => c.endsWith("_name"));
  const lead = [idCol, nameCol].filter((c): c is string => !!c);
  const rest = cols.filter((c) => !lead.includes(c));
  return [...lead, ...rest];
}

/**
 * 列を「表の既定列（primary）」と「メタデータ列」に分けて返す。
 * primary は表示名（name / *_name）を先頭に寄せ、残りは出現順を保つ。
 * メタデータ（ID・space_id・ベクトル）は既定で非表示にし、詳細ペイン下部で見せる。
 */
export function classifyColumns(rows: Record<string, unknown>[]): {
  primary: string[];
  metadata: string[];
} {
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
  const metadata = cols.filter((c) => isMetadataColumn(c));
  const rest = cols.filter((c) => !isMetadataColumn(c));
  const names = rest.filter((c) => c === "name" || c.endsWith("_name"));
  const others = rest.filter((c) => !(c === "name" || c.endsWith("_name")));
  return { primary: [...names, ...others], metadata };
}
