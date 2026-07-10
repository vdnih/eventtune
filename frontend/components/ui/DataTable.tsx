"use client";

import { ChevronDown, ChevronsUpDown, ChevronUp } from "lucide-react";

import { cn } from "@/lib/utils";
import { formatCell } from "./format";
import type { DisplayRow, SortState } from "@/app/(app)/data/useTableView";

/**
 * 汎用データテーブル。dict 配列を受け取り、指定された可視列を描画する。
 * ソート・列フィルタは呼び出し側（useTableView）が状態を持ち、本コンポーネントは presentational。
 * モデル固有の知識は持たない（オントロジー変更に追従するため）。
 */
export function DataTable({
  displayRows,
  columns,
  selectedIndex,
  onSelectRow,
  sort,
  onSort,
  filters,
  onFilter,
}: {
  displayRows: DisplayRow[];
  columns: string[];
  selectedIndex: number | null;
  onSelectRow: (index: number) => void;
  sort: SortState;
  onSort: (key: string) => void;
  filters: Record<string, string>;
  onFilter: (key: string, term: string) => void;
}) {
  if (columns.length === 0) {
    return <div className="p-8 text-center text-sm text-gray-400">データがありません</div>;
  }

  return (
    <div className="overflow-auto h-full">
      <table className="w-full text-sm border-collapse">
        <thead className="sticky top-0 bg-gray-50 z-10">
          <tr>
            {columns.map((col) => {
              const active = sort?.key === col;
              return (
                <th
                  key={col}
                  className="text-left font-medium text-gray-500 px-3 py-2 border-b border-gray-200 whitespace-nowrap"
                >
                  <button
                    onClick={() => onSort(col)}
                    className="flex items-center gap-1 hover:text-gray-700"
                    title="クリックで並び替え"
                  >
                    <span>{col}</span>
                    {!active && <ChevronsUpDown className="w-3 h-3 text-gray-300" />}
                    {active && sort?.dir === "asc" && <ChevronUp className="w-3 h-3 text-brand-600" />}
                    {active && sort?.dir === "desc" && (
                      <ChevronDown className="w-3 h-3 text-brand-600" />
                    )}
                  </button>
                </th>
              );
            })}
          </tr>
          <tr>
            {columns.map((col) => (
              <th key={col} className="px-2 py-1 border-b border-gray-200 bg-gray-50">
                <input
                  value={filters[col] ?? ""}
                  onChange={(e) => onFilter(col, e.target.value)}
                  placeholder="絞り込み"
                  className="w-full min-w-[90px] px-1.5 py-0.5 text-xs font-normal border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-brand-400"
                />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displayRows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-8 text-center text-sm text-gray-400">
                該当するデータがありません
              </td>
            </tr>
          ) : (
            displayRows.map(({ row, index }) => (
              <tr
                key={index}
                onClick={() => onSelectRow(index)}
                className={cn(
                  "cursor-pointer border-b border-gray-100 hover:bg-brand-50",
                  selectedIndex === index && "bg-brand-50",
                )}
              >
                {columns.map((col) => (
                  <td
                    key={col}
                    className="px-3 py-2 text-gray-700 max-w-[280px] truncate"
                    title={formatCell(row[col], col)}
                  >
                    {formatCell(row[col], col)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
