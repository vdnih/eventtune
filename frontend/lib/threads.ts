/**
 * スレッド API クライアント
 *
 * チャットの会話（スレッド）一覧・本文・リネーム・削除を扱う薄い関数群。
 * すべて authFetch 経由でバックエンド（/api/marketing/threads...）を呼ぶ。
 */
import { authFetch } from "@/lib/api";

export interface ThreadSummary {
  thread_id: string;
  title: string;
  updated_at: string;
  message_count?: number;
}

// Firestore に保存された再表示用メッセージ（スナップショット）
export interface StoredMessage {
  seq: number;
  role: "user" | "assistant";
  content: string;
  tool_calls?: { tool_name: string; args: Record<string, unknown> }[];
  code_blocks?: { code: string; output?: string; outcome?: string }[];
  run_id?: string | null;
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
