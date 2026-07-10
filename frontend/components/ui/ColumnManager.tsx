"use client";

import { ChevronDown, ChevronUp, Eye, EyeOff, RotateCcw, X } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * 列の表示/非表示と並び替えを行うパネル。dnd ライブラリは使わず上下ボタンで並べ替える。
 * メタデータ列（ID・ベクトル等）もここから再表示できる。
 */
export function ColumnManager({
  order,
  hidden,
  metadataColumns,
  onToggle,
  onMove,
  onReset,
  onClose,
}: {
  order: string[];
  hidden: string[];
  metadataColumns: string[];
  onToggle: (key: string) => void;
  onMove: (key: string, dir: "up" | "down") => void;
  onReset: () => void;
  onClose: () => void;
}) {
  return (
    <div className="absolute right-3 top-11 z-30 w-72 max-h-[70vh] overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg">
      <div className="flex items-center justify-between px-3 h-10 border-b border-gray-100 sticky top-0 bg-white">
        <span className="text-xs font-semibold text-gray-500">表示列の設定</span>
        <div className="flex items-center gap-2">
          <button
            onClick={onReset}
            className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600"
            title="既定に戻す"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            リセット
          </button>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
      <ul className="py-1">
        {order.map((key, i) => {
          const isHidden = hidden.includes(key);
          const isMeta = metadataColumns.includes(key);
          return (
            <li
              key={key}
              className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 text-sm"
            >
              <button
                onClick={() => onToggle(key)}
                className={cn("shrink-0", isHidden ? "text-gray-300" : "text-brand-600")}
                title={isHidden ? "表示する" : "非表示にする"}
              >
                {isHidden ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
              <span
                className={cn(
                  "flex-1 truncate",
                  isHidden ? "text-gray-400" : "text-gray-700",
                )}
                title={key}
              >
                {key}
                {isMeta && (
                  <span className="ml-1 text-[10px] text-gray-400">メタ</span>
                )}
              </span>
              <div className="shrink-0 flex flex-col">
                <button
                  onClick={() => onMove(key, "up")}
                  disabled={i === 0}
                  className="text-gray-300 hover:text-gray-600 disabled:opacity-30"
                >
                  <ChevronUp className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => onMove(key, "down")}
                  disabled={i === order.length - 1}
                  className="text-gray-300 hover:text-gray-600 disabled:opacity-30"
                >
                  <ChevronDown className="w-3.5 h-3.5" />
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
