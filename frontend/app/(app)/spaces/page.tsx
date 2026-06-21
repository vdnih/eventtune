"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { Plus, Check, ArrowRight } from "lucide-react";
import { useSpace } from "@/lib/space-context";

export default function SpacesPage() {
  const router = useRouter();
  const { spaces, activeSpace, switchSpace } = useSpace();

  return (
    <div className="h-full overflow-auto p-8">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-gray-900">スペース</h1>
          <Link
            href="/spaces/new"
            className="flex items-center gap-1.5 px-3 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
          >
            <Plus className="w-4 h-4" /> 新規作成
          </Link>
        </div>

        {spaces.length === 0 ? (
          <p className="text-sm text-gray-500">所属しているスペースがありません。新しく作成してください。</p>
        ) : (
          <div className="space-y-2">
            {spaces.map((s) => {
              const active = s.space_id === activeSpace?.space_id;
              return (
                <button
                  key={s.space_id}
                  onClick={() => {
                    switchSpace(s.space_id);
                    router.push("/dashboard");
                  }}
                  className={`w-full flex items-center justify-between px-4 py-3 border rounded-lg text-left hover:bg-gray-50 ${
                    active ? "border-brand-500 bg-brand-50/40" : "border-gray-200"
                  }`}
                >
                  <div>
                    <div className="font-medium text-gray-900">{s.name}</div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      {s.role === "owner" ? "オーナー" : "メンバー"}
                    </div>
                  </div>
                  {active ? (
                    <span className="flex items-center gap-1 text-xs text-brand-600">
                      <Check className="w-4 h-4" /> 選択中
                    </span>
                  ) : (
                    <ArrowRight className="w-4 h-4 text-gray-300" />
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
