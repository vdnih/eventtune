/**
 * 取り込みプラン（BatchPlan）の型と表示用ラベル
 *
 * POST /api/integration/plan のレスポンス = BatchPlan。ユーザーが確認・修正したものを
 * そのまま POST /batches の plan に渡す（承認と実行の契約。ADR-015）。
 * page.tsx / IngestionPlanCard.tsx / lib/threads.ts の複数箇所から参照するため共通化する。
 */

export interface TargetPlan {
  entity_type: string;
  column_map: Record<string, string>;
  column_modes: Record<string, string>; // {元列: "direct" | "ai_parse"}
  link_columns: Record<string, string>; // {リンク種別: 元列}
}

export interface FilePlan {
  filename: string;
  business_context: string;
  targets: TargetPlan[];
  unmapped_notes: string;
  extraction_caveat: string;
}

export interface DefaultEventPlan {
  name: string;
  is_existing: boolean;
  evidence: string;
}

export interface BatchPlan {
  default_event: DefaultEventPlan | null;
  files: FilePlan[];
}

export const ENTITY_LABEL: Record<string, string> = {
  persons: "人物",
  accounts: "企業",
  events: "イベント",
  products: "製品",
  contents: "コンテンツ",
  event_attendances: "イベント参加（接客）",
  product_interests: "製品関心",
  cost_items: "費用",
  event_kpi: "イベントKPI",
  survey_summary: "アンケート集計",
};

export const LINK_KIND_LABEL: Record<string, string> = {
  event: "イベント",
  account: "企業",
  product: "製品",
};
