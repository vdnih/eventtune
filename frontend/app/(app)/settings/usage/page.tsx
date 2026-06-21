"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Cpu, Coins } from "lucide-react";
import { authFetch } from "@/lib/api";
import { useSpace } from "@/lib/space-context";

interface UsageResponse {
  period: string;
  plan: string;
  usage: {
    llm: Record<string, { input_tokens?: number; output_tokens?: number }>;
    compute: Record<string, { ms?: number }>;
  };
  credits_used: number;
  credit_limit: number;
  credit_remaining: number;
}

export default function UsagePage() {
  const { activeSpace } = useSpace();
  const [data, setData] = useState<UsageResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!activeSpace) return;
    setLoading(true);
    try {
      const res = await authFetch(`/api/spaces/${activeSpace.space_id}/usage`);
      if (res.ok) setData(await res.json());
    } finally {
      setLoading(false);
    }
  }, [activeSpace]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading || !data) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
      </div>
    );
  }

  const pct = data.credit_limit > 0 ? Math.min((data.credits_used / data.credit_limit) * 100, 100) : 0;

  return (
    <div className="h-full overflow-auto p-8">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-baseline justify-between mb-1">
          <h1 className="text-2xl font-bold text-gray-900">利用状況</h1>
          <span className="text-sm text-gray-400">{data.period}</span>
        </div>
        <p className="text-sm text-gray-500 mb-6">
          プラン <span className="font-medium uppercase">{data.plan}</span> ・
          リソース消費（生成AIトークンと処理時間）から換算したクレジット消費を表示します。
        </p>

        {/* クレジット（主指標） */}
        <div className="border border-gray-200 rounded-lg p-5 mb-6">
          <div className="flex items-center gap-2 mb-3">
            <Coins className="w-5 h-5 text-brand-600" />
            <span className="font-semibold text-gray-900">クレジット消費</span>
          </div>
          <div className="flex items-baseline gap-2 mb-2">
            <span className="text-3xl font-bold text-gray-900">{data.credits_used.toLocaleString()}</span>
            <span className="text-sm text-gray-500">/ {data.credit_limit.toLocaleString()} クレジット</span>
          </div>
          <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${pct > 90 ? "bg-red-500" : "bg-brand-500"}`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="text-xs text-gray-400 mt-1.5">残り {data.credit_remaining.toLocaleString()} クレジット</div>
        </div>

        {/* 内訳: LLMトークン */}
        <h2 className="text-sm font-semibold text-gray-700 mb-2">AIトークン（モデル別）</h2>
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100 mb-6">
          {Object.keys(data.usage.llm).length === 0 ? (
            <div className="px-4 py-3 text-sm text-gray-400">利用なし</div>
          ) : (
            Object.entries(data.usage.llm).map(([model, t]) => (
              <div key={model} className="flex items-center justify-between px-4 py-3 text-sm">
                <span className="text-gray-700">{model}</span>
                <span className="text-gray-500">
                  入力 {(t.input_tokens ?? 0).toLocaleString()} / 出力 {(t.output_tokens ?? 0).toLocaleString()} tok
                </span>
              </div>
            ))
          )}
        </div>

        {/* 内訳: コンピュート */}
        <h2 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-1.5">
          <Cpu className="w-4 h-4 text-gray-400" /> 処理時間（リソース種別別）
        </h2>
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          {Object.keys(data.usage.compute).length === 0 ? (
            <div className="px-4 py-3 text-sm text-gray-400">利用なし</div>
          ) : (
            Object.entries(data.usage.compute).map(([rtype, c]) => (
              <div key={rtype} className="flex items-center justify-between px-4 py-3 text-sm">
                <span className="text-gray-700">{rtype}</span>
                <span className="text-gray-500">{((c.ms ?? 0) / 1000).toFixed(1)} 秒</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
