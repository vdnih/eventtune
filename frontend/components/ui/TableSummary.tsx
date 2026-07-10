"use client";

import { useEffect, useState } from "react";
import { Loader2, RefreshCw, Sparkles } from "lucide-react";

import { authFetch } from "@/lib/api";
import { formatTimestamp } from "./format";
import type { ColumnStats } from "./aggregate";

function num(n: number): string {
  return Number.isInteger(n) ? n.toLocaleString("ja-JP") : n.toLocaleString("ja-JP", {
    maximumFractionDigits: 2,
  });
}

function StatCard({ stats }: { stats: ColumnStats }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50/60 px-3 py-2 text-xs">
      <div className="font-medium text-gray-500 truncate mb-1" title={stats.key}>
        {stats.key}
      </div>
      {stats.kind === "number" && (
        <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-gray-700">
          <span>件数 {num(stats.count)}</span>
          <span>合計 {num(stats.sum)}</span>
          <span>平均 {num(stats.avg)}</span>
          <span>最小 {num(stats.min)}</span>
          <span className="col-span-2">最大 {num(stats.max)}</span>
        </div>
      )}
      {stats.kind === "boolean" && (
        <div className="text-gray-700">
          ✓ {num(stats.trueCount)} / ✗ {num(stats.falseCount)}
        </div>
      )}
      {stats.kind === "category" && (
        <div className="space-y-0.5 text-gray-700">
          <div className="text-gray-400">{stats.distinct} 種類</div>
          {stats.top.map((t) => (
            <div key={t.value} className="flex justify-between gap-2">
              <span className="truncate" title={t.value}>
                {t.value}
              </span>
              <span className="shrink-0 text-gray-400">{num(t.count)}</span>
            </div>
          ))}
        </div>
      )}
      {stats.kind === "other" && (
        <div className="text-gray-400">件数 {num(stats.count)}</div>
      )}
    </div>
  );
}

interface SummaryResponse {
  text?: string;
  generated_at?: string;
  row_count?: number;
}

/**
 * テーブルのサマリパネル。数値集計は常時表示し、AI 生成の要約文はボタン押下で取得する
 * （/api/data/{view}/summary を叩き、バックエンドが Firestore にキャッシュ）。
 */
export function TableSummary({
  viewKey,
  stats,
}: {
  viewKey: string;
  stats: ColumnStats[];
}) {
  const [aiText, setAiText] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  // ビュー切替で AI サマリはリセット（自動生成はしない＝オンデマンド）。
  useEffect(() => {
    setAiText(null);
    setGeneratedAt(null);
    setError(false);
  }, [viewKey]);

  const generate = async (refresh: boolean) => {
    setLoading(true);
    setError(false);
    try {
      const res = await authFetch(`/api/data/${viewKey}/summary?refresh=${refresh}`);
      if (!res.ok) throw new Error(String(res.status));
      const data: SummaryResponse = await res.json();
      setAiText(data.text ?? "");
      setGeneratedAt(data.generated_at ?? null);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  const shownStats = stats.filter((s) => s.kind !== "other");

  return (
    <div className="shrink-0 border-b border-gray-200 bg-white px-3 py-3 space-y-3 max-h-[45vh] overflow-y-auto">
      {/* AI サマリ */}
      <div className="rounded-lg border border-brand-100 bg-brand-50/40 px-3 py-2">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-brand-700">
            <Sparkles className="w-3.5 h-3.5" />
            AIサマリ
          </span>
          {aiText === null ? (
            <button
              onClick={() => generate(false)}
              disabled={loading}
              className="flex items-center gap-1 text-xs text-brand-600 hover:text-brand-700 disabled:opacity-50"
            >
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
              生成する
            </button>
          ) : (
            <button
              onClick={() => generate(true)}
              disabled={loading}
              className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 disabled:opacity-50"
            >
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              再生成
            </button>
          )}
        </div>
        {error && <p className="mt-1 text-xs text-red-500">生成に失敗しました</p>}
        {aiText !== null && !error && (
          <p className="mt-1.5 text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
            {aiText || "（要約を生成できませんでした）"}
          </p>
        )}
        {generatedAt && (
          <p className="mt-1 text-[10px] text-gray-400">
            生成: {formatTimestamp(generatedAt) ?? generatedAt}
          </p>
        )}
      </div>

      {/* 数値・分布サマリ */}
      {shownStats.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {shownStats.map((s) => (
            <StatCard key={s.key} stats={s} />
          ))}
        </div>
      )}
    </div>
  );
}
