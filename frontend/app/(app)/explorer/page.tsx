"use client";

import { useCallback, useEffect, useState } from "react";
import { Search } from "lucide-react";
import { authFetch } from "@/lib/api";
import { DataTable } from "@/components/ui/DataTable";
import { Drawer } from "@/components/ui/Drawer";
import { formatDetail, isComplex, pickEntityId } from "@/components/ui/format";

type Collection = { key: string; label: string };
type Row = Record<string, unknown>;
type LineageReport = Record<string, unknown>;

/**
 * 汎用データエクスプローラー。
 * バックエンド主導（/api/data）で全データモデルを横断閲覧する薄いビュー。
 * 編集はチャットの AI エージェントに委ね、ここは閲覧＋由来逆引きに特化する。
 */
export default function ExplorerPage() {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  // 左メニュー: コレクション一覧
  useEffect(() => {
    authFetch("/api/data/collections")
      .then((r) => r.json())
      .then((d) => {
        const cols: Collection[] = d.collections ?? [];
        setCollections(cols);
        if (cols.length > 0) setActiveKey(cols[0].key);
      })
      .catch(() => setCollections([]));
  }, []);

  // 中央: 選択ビューの行を取得
  useEffect(() => {
    if (!activeKey) return;
    setLoading(true);
    setSelectedIndex(null);
    authFetch(`/api/data/${activeKey}`)
      .then((r) => r.json())
      .then((d) => setRows(d.rows ?? []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [activeKey]);

  const selectedRow = selectedIndex !== null ? rows[selectedIndex] : null;

  return (
    <div className="h-full flex">
      {/* 左: コレクションセレクタ */}
      <aside className="w-56 shrink-0 border-r border-gray-200 bg-white overflow-auto">
        <div className="px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">
          データ
        </div>
        <nav className="px-2 pb-4 space-y-0.5">
          {collections.map((c) => (
            <button
              key={c.key}
              onClick={() => setActiveKey(c.key)}
              className={`w-full text-left px-3 py-2 rounded-md text-sm ${
                activeKey === c.key
                  ? "bg-brand-50 text-brand-700 font-medium"
                  : "text-gray-600 hover:bg-gray-50"
              }`}
            >
              {c.label}
            </button>
          ))}
        </nav>
      </aside>

      {/* 中央: 一覧テーブル */}
      <section className="flex-1 min-w-0 flex flex-col bg-white">
        <div className="shrink-0 px-4 py-3 border-b border-gray-200 flex items-center justify-between">
          <h1 className="font-semibold text-gray-800">
            {collections.find((c) => c.key === activeKey)?.label ?? "データ"}
          </h1>
          <span className="text-sm text-gray-400">{rows.length} 件</span>
        </div>
        <div className="flex-1 min-h-0">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <div className="w-6 h-6 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : (
            <DataTable rows={rows} selectedIndex={selectedIndex} onSelectRow={setSelectedIndex} />
          )}
        </div>
      </section>

      {/* 右: 選択行の詳細 + 由来を追う */}
      {selectedRow && (
        <DetailPane row={selectedRow} onClose={() => setSelectedIndex(null)} />
      )}
    </div>
  );
}

function DetailPane({ row, onClose }: { row: Row; onClose: () => void }) {
  const [lineageOpen, setLineageOpen] = useState(false);
  const [lineage, setLineage] = useState<LineageReport | null>(null);
  const [lineageLoading, setLineageLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);

  const entityId = pickEntityId(row);

  const traceLineage = useCallback(() => {
    if (!entityId) return;
    setLineageOpen(true);
    setLineageLoading(true);
    setNotFound(false);
    authFetch(`/api/data/lineage/by-entity/${encodeURIComponent(entityId)}`)
      .then((r) => r.json())
      .then((d) => {
        if (d.report) setLineage(d.report);
        else setNotFound(true);
      })
      .catch(() => setNotFound(true))
      .finally(() => setLineageLoading(false));
  }, [entityId]);

  return (
    <aside className="w-80 shrink-0 border-l border-gray-200 bg-white overflow-auto">
      <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
        <h2 className="font-semibold text-gray-800 text-sm">詳細</h2>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-sm">
          閉じる
        </button>
      </div>

      {entityId && (
        <div className="px-4 py-3 border-b border-gray-200">
          <button
            onClick={traceLineage}
            className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-sm rounded-md border border-brand-200 text-brand-700 hover:bg-brand-50"
          >
            <Search className="w-4 h-4" /> 由来を追う
          </button>
        </div>
      )}

      <dl className="px-4 py-3 space-y-3">
        {Object.entries(row).map(([key, value]) => (
          <div key={key}>
            <dt className="text-xs font-medium text-gray-400">{key}</dt>
            <dd className="mt-0.5 text-sm text-gray-800 break-words">
              {isComplex(value) ? (
                <pre className="text-xs bg-gray-50 rounded p-2 overflow-auto whitespace-pre-wrap">
                  {formatDetail(value)}
                </pre>
              ) : (
                formatDetail(value)
              )}
            </dd>
          </div>
        ))}
      </dl>

      <Drawer open={lineageOpen} title="データの由来" onClose={() => setLineageOpen(false)}>
        {lineageLoading ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-6 h-6 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : notFound ? (
          <p className="text-sm text-gray-500">
            このレコードの由来（取り込み来歴）は見つかりませんでした。手動作成やエージェント生成のデータには来歴が無い場合があります。
          </p>
        ) : (
          <pre className="text-xs bg-gray-50 rounded p-3 overflow-auto whitespace-pre-wrap">
            {formatDetail(lineage)}
          </pre>
        )}
      </Drawer>
    </aside>
  );
}
