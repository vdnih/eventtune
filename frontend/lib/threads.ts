/**
 * スレッド API クライアント
 *
 * チャットの会話（スレッド）一覧・本文・リネーム・削除を扱う薄い関数群。
 * すべて authFetch 経由でバックエンド（/api/marketing/threads...）を呼ぶ。
 */
import { authFetch } from "@/lib/api";
import type { BatchPlan } from "@/lib/ingestion";

export interface ThreadSummary {
  thread_id: string;
  title: string;
  updated_at: string;
  message_count?: number;
}

// Firestore に保存された再表示用メッセージ（スナップショット）。
// chat・ingestion 両方のターンが同じスレッドの中に seq 順で並ぶ（content_type で見分ける）。
export interface StoredMessage {
  seq: number;
  role: "user" | "assistant";
  content: string;
  tool_calls?: { tool_name: string; args: Record<string, unknown>; intent?: string }[];
  code_blocks?: { code: string; output?: string; outcome?: string; intent?: string }[];
  run_id?: string | null;
  run_source?: string | null; // run_id を発行したツール名（"run_assembly" | "generate_individual_deliverables"）
  pattern_segment_id?: string | null;
  pattern_format?: string | null;
  files?: string[]; // ingestion のユーザー添付メッセージのみで使う
  // ingestion 関連メッセージのみで使うフィールド
  content_type?: "ingestion_plan" | "ingestion_confirm" | "ingestion_result" | "ingestion_error";
  batch_id?: string;
  plan?: BatchPlan | null;
  filenames?: string[];
  report_markdown?: string;
  created_entities?: Record<string, number>;
  pending_count?: number;
  skipped_count?: number;
  error?: string;
}

export async function listThreads(): Promise<ThreadSummary[]> {
  const res = await authFetch("/api/marketing/threads");
  if (!res.ok) return [];
  const data = await res.json();
  return (data.threads ?? []) as ThreadSummary[];
}

export async function getThreadMessages(threadId: string): Promise<StoredMessage[]> {
  const res = await authFetch(`/api/marketing/threads/${threadId}/messages`);
  if (!res.ok) return [];
  const data = await res.json();
  return (data.messages ?? []) as StoredMessage[];
}

export async function renameThread(threadId: string, title: string): Promise<void> {
  await authFetch(`/api/marketing/threads/${threadId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export async function deleteThread(threadId: string): Promise<void> {
  await authFetch(`/api/marketing/threads/${threadId}`, { method: "DELETE" });
}
