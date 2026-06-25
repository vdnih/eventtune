"use client";

import { formatCell, unionColumns } from "./format";

/**
 * 汎用データテーブル。dict 配列を受け取り、全行のキーの和集合を列にして描画する。
 * モデル固有の知識は持たない（オントロジー変更に追従するため）。
 */
export function DataTable({
  rows,
  selectedIndex,
  onSelectRow,
}: {
  rows: Record<string, unknown>[];
  selectedIndex: number | null;
  onSelectRow: (index: number) => void;
}) {
  if (rows.length === 0) {
    return <div className="p-8 text-center text-sm text-gray-400">データがありません</div>;
  }

  const columns = unionColumns(rows);

  return (
    <div className="overflow-auto h-full">
      <table className="w-full text-sm border-collapse">
        <thead className="sticky top-0 bg-gray-50 z-10">
          <tr>
            {columns.map((col) => (
              <th
                key={col}
                className="text-left font-medium text-gray-500 px-3 py-2 border-b border-gray-200 whitespace-nowrap"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              onClick={() => onSelectRow(i)}
              className={`cursor-pointer border-b border-gray-100 hover:bg-brand-50 ${
                selectedIndex === i ? "bg-brand-50" : ""
              }`}
            >
              {columns.map((col) => (
                <td
                  key={col}
                  className="px-3 py-2 text-gray-700 max-w-[280px] truncate"
                  title={formatCell(row[col])}
                >
                  {formatCell(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
