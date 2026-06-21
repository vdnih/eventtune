"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Trash2 } from "lucide-react";
import { authFetch, setActiveSpaceId } from "@/lib/api";
import { useSpace } from "@/lib/space-context";

interface SpaceDetail {
  space_id: string;
  name: string;
  description?: string;
  plan: string;
  role: string;
}

export default function SpaceSettingsPage() {
  const router = useRouter();
  const { activeSpace, reloadSpaces } = useSpace();
  const [detail, setDetail] = useState<SpaceDetail | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!activeSpace) return;
    const res = await authFetch(`/api/spaces/${activeSpace.space_id}`);
    if (!res.ok) return;
    const data: SpaceDetail = await res.json();
    setDetail(data);
    setName(data.name);
    setDescription(data.description ?? "");
  }, [activeSpace]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!activeSpace) return;
    setSaving(true);
    setMsg(null);
    try {
      const res = await authFetch(`/api/spaces/${activeSpace.space_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), description: description.trim() }),
      });
      if (!res.ok) throw new Error();
      await reloadSpaces();
      setMsg("保存しました");
    } catch {
      setMsg("保存に失敗しました");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!activeSpace) return;
    if (!confirm(`スペース「${activeSpace.name}」と配下の全データを完全に削除します。よろしいですか？`)) return;
    const res = await authFetch(`/api/spaces/${activeSpace.space_id}`, { method: "DELETE" });
    if (res.ok) {
      setActiveSpaceId(null);
      const list = await reloadSpaces();
      router.replace(list.length > 0 ? "/dashboard" : "/spaces/new");
    }
  }

  if (!detail) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-8">
      <div className="max-w-lg mx-auto">
        <h1 className="text-2xl font-bold text-gray-900 mb-6">スペース設定</h1>

        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">スペース名</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">説明</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
          <div className="text-sm text-gray-500">
            プラン: <span className="font-medium text-gray-700 uppercase">{detail.plan}</span>
          </div>
          {msg && <p className="text-sm text-gray-600">{msg}</p>}
          <button
            type="submit"
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {saving && <Loader2 className="w-4 h-4 animate-spin" />}
            保存
          </button>
        </form>

        <div className="mt-10 pt-6 border-t border-gray-200">
          <h2 className="text-sm font-semibold text-red-600 mb-2">危険な操作</h2>
          <button
            onClick={handleDelete}
            className="flex items-center gap-2 px-4 py-2 border border-red-300 text-red-600 text-sm font-medium rounded-md hover:bg-red-50"
          >
            <Trash2 className="w-4 h-4" /> このスペースを削除
          </button>
        </div>
      </div>
    </div>
  );
}
