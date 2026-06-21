"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, UserPlus, Trash2, Crown } from "lucide-react";
import { authFetch } from "@/lib/api";
import { useSpace } from "@/lib/space-context";

interface Member {
  user_id: string;
  email: string;
  role: string;
}

export default function MembersPage() {
  const { activeSpace } = useSpace();
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [inviting, setInviting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!activeSpace) return;
    setLoading(true);
    try {
      const res = await authFetch(`/api/spaces/${activeSpace.space_id}/members`);
      if (res.ok) setMembers((await res.json()).members ?? []);
    } finally {
      setLoading(false);
    }
  }, [activeSpace]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    if (!activeSpace || !email.trim()) return;
    setInviting(true);
    setError(null);
    try {
      const res = await authFetch(`/api/spaces/${activeSpace.space_id}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), role }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー ${res.status}`);
      }
      setEmail("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "招待に失敗しました");
    } finally {
      setInviting(false);
    }
  }

  async function handleRemove(uid: string, memberEmail: string) {
    if (!activeSpace) return;
    if (!confirm(`${memberEmail} をスペースから削除しますか？`)) return;
    const res = await authFetch(`/api/spaces/${activeSpace.space_id}/members/${uid}`, {
      method: "DELETE",
    });
    if (res.ok) await load();
    else {
      const body = await res.json().catch(() => ({}));
      alert(body.detail ?? "削除に失敗しました");
    }
  }

  return (
    <div className="h-full overflow-auto p-8">
      <div className="max-w-2xl mx-auto">
        <h1 className="text-2xl font-bold text-gray-900 mb-6">メンバー管理</h1>

        <form onSubmit={handleInvite} className="flex items-end gap-2 mb-6">
          <div className="flex-1">
            <label className="block text-sm font-medium text-gray-700 mb-1">メールで招待</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="member@example.com"
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded-md text-sm h-[38px]"
          >
            <option value="member">メンバー</option>
            <option value="owner">オーナー</option>
          </select>
          <button
            type="submit"
            disabled={inviting || !email.trim()}
            className="flex items-center gap-1.5 px-4 h-[38px] bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {inviting ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserPlus className="w-4 h-4" />}
            招待
          </button>
        </form>
        {error && <p className="text-sm text-red-600 mb-4">{error}</p>}

        {loading ? (
          <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
        ) : (
          <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
            {members.map((m) => (
              <div key={m.user_id} className="flex items-center justify-between px-4 py-3">
                <div className="flex items-center gap-2">
                  {m.role === "owner" && <Crown className="w-4 h-4 text-amber-500" />}
                  <span className="text-sm text-gray-900">{m.email}</span>
                  <span className="text-xs text-gray-400">
                    {m.role === "owner" ? "オーナー" : "メンバー"}
                  </span>
                </div>
                {m.role !== "owner" && (
                  <button
                    onClick={() => handleRemove(m.user_id, m.email)}
                    className="text-gray-400 hover:text-red-600"
                    title="削除"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
