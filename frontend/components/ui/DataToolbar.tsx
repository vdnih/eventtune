"use client";

import { BarChart3, Columns3, Search, X } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * データテーブル上部のツールバー。全文検索・列マネージャ開閉・サマリ開閉・フィルタ解除。
 */
export function DataToolbar({
  search,
  onSearch,
  rowCount,
  totalCount,
  filterActive,
  onClearFilters,
  columnsOpen,
  onToggleColumns,
  summaryOpen,
  onToggleSummary,
}: {
  search: string;
  onSearch: (value: string) => void;
  rowCount: number;
  totalCount: number;
  filterActive: boolean;
  onClearFilters: () => void;
  columnsOpen: boolean;
  onToggleColumns: () => void;
  summaryOpen: boolean;
  onToggleSummary: () => void;
}) {
  return (
    <div className="shrink-0 flex items-center gap-2 px-3 h-11 border-b border-gray-200 bg-white">
      <div className="relative">
        <Search className="w-4 h-4 text-gray-400 absolute left-2 top-1/2 -translate-y-1/2" />
        <input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="検索…"
          className="w-56 pl-8 pr-2 py-1 text-sm border border-gray-200 rounded-md focus:outline-none focus:ring-1 focus:ring-brand-400"
        />
      </div>

      <span className="text-xs text-gray-400">
        {rowCount === totalCount ? `${totalCount} 件` : `${rowCount} / ${totalCount} 件`}
      </span>

      {filterActive && (
        <button
          onClick={onClearFilters}
          className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700"
        >
          <X className="w-3.5 h-3.5" />
          絞り込み解除
        </button>
      )}

      <div className="ml-auto flex items-center gap-1">
        <button
          onClick={onToggleSummary}
          className={cn(
            "flex items-center gap-1 px-2 py-1 text-xs rounded-md transition",
            summaryOpen ? "bg-brand-50 text-brand-700" : "text-gray-600 hover:bg-gray-50",
          )}
        >
          <BarChart3 className="w-3.5 h-3.5" />
          サマリ
        </button>
        <button
          onClick={onToggleColumns}
          className={cn(
            "flex items-center gap-1 px-2 py-1 text-xs rounded-md transition",
            columnsOpen ? "bg-brand-50 text-brand-700" : "text-gray-600 hover:bg-gray-50",
          )}
        >
          <Columns3 className="w-3.5 h-3.5" />
          列
        </button>
      </div>
    </div>
  );
}
