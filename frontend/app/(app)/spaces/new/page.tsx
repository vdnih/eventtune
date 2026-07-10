"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { authFetch, setActiveSpaceId } from "@/lib/api";
import { useSpace } from "@/lib/space-context";

export default function NewSpacePage() {
  const router = useRouter();
  const { reloadSpaces, switchSpace } = useSpace();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await authFetch("/api/spaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), description: description.trim() }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー ${res.status}`);
      }
      const created = await res.json();
      // 作成したスペースをアクティブにしてダッシュボードへ
      setActiveSpaceId(created.space_id);
      await reloadSpaces();
      switchSpace(created.space_id);
      router.replace("/agent");
    } catch (err) {
      setError(err instanceof Error ? err.message : "作成に失敗しました");
      setSubmitting(false);
    }
  }

  return (
    <div className="h-full overflow-auto p-8">
      <div className="max-w-lg mx-auto">
        <h1 className="text-2xl font-bold text-gray-900 mb-2">スペースを作成</h1>
        <p className="text-sm text-gray-500 mb-6">
          スペースはマーケティングチーム単位の作業領域です。データはスペースごとに分離され、
          招待したメンバーと共同で作業できます。
        </p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">スペース名</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例: マーケティング部"
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">説明（任意）</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={submitting || !name.trim()}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            スペースを作成
          </button>
        </form>
      </div>
    </div>
  );
}
