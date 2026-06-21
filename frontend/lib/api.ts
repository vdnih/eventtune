/**
 * 中央 API クライアント
 *
 * すべてのバックエンド呼び出しに Firebase IDトークン（Authorization）と
 * アクティブスペース（X-Space-Id）を付与する。アクティブスペースIDは
 * SpaceProvider が setActiveSpaceId で更新し、localStorage にも保持する。
 *
 * セキュリティ補足: X-Space-Id はクライアント側の「主張」にすぎず、バックエンドが
 * 検証済み uid × membership で認可を再導出する（Space-ID Trust Boundary）。
 */
import { auth } from "@/lib/firebase";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ACTIVE_SPACE_KEY = "activeSpaceId";

// React 外（authFetch など）からも参照できるようモジュール変数で保持する
let activeSpaceId: string | null =
  typeof window !== "undefined" ? window.localStorage.getItem(ACTIVE_SPACE_KEY) : null;

export function getActiveSpaceId(): string | null {
  return activeSpaceId;
}

export function setActiveSpaceId(id: string | null): void {
  activeSpaceId = id;
  if (typeof window === "undefined") return;
  if (id) window.localStorage.setItem(ACTIVE_SPACE_KEY, id);
  else window.localStorage.removeItem(ACTIVE_SPACE_KEY);
}

export async function getToken(): Promise<string> {
  return (await auth.currentUser?.getIdToken()) ?? "";
}

/** Authorization と X-Space-Id を含むヘッダを構築する（SSE/ダウンロード等の生 fetch 用）。 */
export async function authHeaders(
  extra?: Record<string, string>,
): Promise<Record<string, string>> {
  const token = await getToken();
  const headers: Record<string, string> = { Authorization: `Bearer ${token}`, ...extra };
  if (activeSpaceId) headers["X-Space-Id"] = activeSpaceId;
  return headers;
}

/** 認証 + スペース付きの fetch。ほとんどの API 呼び出しはこれを使う。 */
export async function authFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = await authHeaders(init?.headers as Record<string, string> | undefined);
  return fetch(`${API_BASE}${path}`, { ...init, headers });
}
