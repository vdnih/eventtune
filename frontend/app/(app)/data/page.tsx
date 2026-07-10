"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { ChevronRight, GitBranch, Loader2 } from "lucide-react";
import { useSpace } from "@/lib/space-context";
import { authFetch } from "@/lib/api";
import { DataTable } from "@/components/ui/DataTable";
import { DataToolbar } from "@/components/ui/DataToolbar";
import { ColumnManager } from "@/components/ui/ColumnManager";
import { TableSummary } from "@/components/ui/TableSummary";
import { Drawer } from "@/components/ui/Drawer";
import { formatDetail, isComplex, isMetadataColumn, pickEntityId } from "@/components/ui/format";
import { summarize } from "@/components/ui/aggregate";
import { useTableView } from "./useTableView";

interface Collection {
  key: string;
  label: string;
  group: string;
}

interface LineageNode {
  job_id?: string;
  filenames?: string[];
  created_at?: string;
  [key: string]: unknown;
}

export default function ExplorerPage() {
  const { activeSpace } = useSpace();
  const spaceId = activeSpace?.space_id ?? null;

  const [collections, setCollections] = useState<Collection[]>([]);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [loadingCols, setLoadingCols] = useState(false);
  const [loadingRows, setLoadingRows] = useState(false);

  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const selectedRow = selectedIndex !== null ? rows[selectedIndex] : null;

  const [columnsOpen, setColumnsOpen] = useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [lineage, setLineage] = useState<LineageNode[]>([]);
  const [loadingLineage, setLoadingLineage] = useState(false);

  const view = useTableView(rows, spaceId, activeKey);

  const stats = useMemo(
    () => summarize(view.displayRows.map((d) => d.row), view.visibleColumns),
    [view.displayRows, view.visibleColumns],
  );

  const fetchJson = useCallback(async (path: string) => {
    const res = await authFetch(path);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }, []);

  // Load collection list
  useEffect(() => {
    if (!spaceId) return;
    setLoadingCols(true);
    fetchJson("/api/data/collections")
      .then((data) => {
        const cols: Collection[] = data.collections ?? [];
        setCollections(cols);
        if (cols.length > 0) setActiveKey(cols[0].key);
      })
      .catch(console.error)
      .finally(() => setLoadingCols(false));
  }, [spaceId, fetchJson]);

  // Load rows when active collection changes
  useEffect(() => {
    if (!activeKey || !spaceId) return;
    setLoadingRows(true);
    setRows([]);
    setSelectedIndex(null);
    setColumnsOpen(false);
    fetchJson(`/api/data/${activeKey}`)
      .then((data) => setRows(data.rows ?? []))
      .catch(console.error)
      .finally(() => setLoadingRows(false));
  }, [activeKey, spaceId, fetchJson]);

  const handleSelectRow = useCallback((index: number) => {
    setSelectedIndex((prev) => (prev === index ? null : index));
  }, []);

  const handleTraceLineage = useCallback(async () => {
    if (!selectedRow) return;
    const entityId = pickEntityId(selectedRow as Record<string, unknown>);
    if (!entityId) return;
    setDrawerOpen(true);
    setLoadingLineage(true);
    try {
      const data = await fetchJson(`/api/data/lineage/by-entity/${entityId}`);
      // backend は単一の source job（{entity_id, job}）を返す。Drawer はリスト表示なので包む。
      setLineage(data.job ? [data.job] : []);
    } catch {
      setLineage([]);
    } finally {
      setLoadingLineage(false);
    }
  }, [selectedRow, fetchJson]);

  const activeLabel = collections.find((c) => c.key === activeKey)?.label ?? activeKey ?? "—";

  // 詳細ペイン: 主要フィールドとメタデータ（ID・ベクトル等）に分割。
  const detailEntries = selectedRow ? Object.entries(selectedRow) : [];
  const primaryEntries = detailEntries.filter(([k]) => !isMetadataColumn(k));
  const metaEntries = detailEntries.filter(([k]) => isMetadataColumn(k));

  return (
    <div className="h-full flex overflow-hidden">
      {/* Left: collection nav */}
      <aside className="w-52 shrink-0 border-r border-gray-200 bg-white overflow-y-auto">
        {loadingCols ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-5 h-5 animate-spin text-gray-300" />
          </div>
        ) : (
          <>
            {Array.from(new Map(collections.map((c) => [c.group, true])).keys()).map((group) => (
              <div key={group}>
                <div className="px-3 pt-3 pb-1 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">
                  {group}
                </div>
                <ul>
                  {collections.filter((c) => c.group === group).map((col) => (
                    <li key={col.key}>
                      <button
                        onClick={() => setActiveKey(col.key)}
                        className={`w-full flex items-center px-3 py-1.5 text-sm transition ${
                          activeKey === col.key
                            ? "bg-brand-50 text-brand-700 font-medium"
                            : "text-gray-700 hover:bg-gray-50"
                        }`}
                      >
                        <span className="truncate">{col.label}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </>
        )}
      </aside>

      {/* Center: toolbar + summary + DataTable */}
      <div className="flex-1 min-w-0 overflow-hidden flex flex-col relative">
        <DataToolbar
          search={view.search}
          onSearch={view.setSearch}
          rowCount={view.displayRows.length}
          totalCount={rows.length}
          filterActive={view.filterActive}
          onClearFilters={view.clearFilters}
          columnsOpen={columnsOpen}
          onToggleColumns={() => setColumnsOpen((v) => !v)}
          summaryOpen={summaryOpen}
          onToggleSummary={() => setSummaryOpen((v) => !v)}
        />

        {columnsOpen && (
          <ColumnManager
            order={view.order}
            hidden={view.hidden}
            metadataColumns={view.metadataColumns}
            onToggle={view.toggleColumn}
            onMove={view.moveColumn}
            onReset={view.resetColumns}
            onClose={() => setColumnsOpen(false)}
          />
        )}

        {summaryOpen && activeKey && !loadingRows && rows.length > 0 && (
          <TableSummary viewKey={activeKey} stats={stats} />
        )}

        <div className="flex-1 min-h-0 overflow-hidden">
          {loadingRows ? (
            <div className="flex justify-center items-center h-full">
              <Loader2 className="w-6 h-6 animate-spin text-gray-300" />
            </div>
          ) : (
            <DataTable
              displayRows={view.displayRows}
              columns={view.visibleColumns}
              selectedIndex={selectedIndex}
              onSelectRow={handleSelectRow}
              sort={view.sort}
              onSort={view.toggleSort}
              filters={view.filters}
              onFilter={view.setFilter}
            />
          )}
        </div>
      </div>

      {/* Right: detail pane */}
      {selectedRow && (
        <aside className="w-72 shrink-0 border-l border-gray-200 bg-white overflow-y-auto">
          <div className="px-4 pt-4 pb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
              {activeLabel} 詳細
            </span>
            <button
              onClick={handleTraceLineage}
              className="flex items-center gap-1 text-xs text-brand-600 hover:text-brand-700"
            >
              <GitBranch className="w-3.5 h-3.5" />
              由来を追う
            </button>
          </div>
          <dl className="divide-y divide-gray-100">
            {primaryEntries.map(([k, v]) => (
              <DetailField key={k} k={k} v={v} />
            ))}
          </dl>

          {metaEntries.length > 0 && (
            <details className="mt-2 border-t border-gray-100">
              <summary className="px-4 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wide cursor-pointer select-none hover:text-gray-600">
                メタデータ
              </summary>
              <dl className="divide-y divide-gray-100">
                {metaEntries.map(([k, v]) => (
                  <DetailField key={k} k={k} v={v} />
                ))}
              </dl>
            </details>
          )}
        </aside>
      )}

      {/* Lineage drawer */}
      <Drawer
        open={drawerOpen}
        title="データ由来（Integration Jobs）"
        onClose={() => setDrawerOpen(false)}
      >
        {loadingLineage ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-gray-300" />
          </div>
        ) : lineage.length === 0 ? (
          <p className="text-sm text-gray-400">由来情報が見つかりませんでした</p>
        ) : (
          <ul className="space-y-3">
            {lineage.map((node, i) => (
              <li key={i} className="border border-gray-100 rounded-lg p-3 text-sm space-y-1">
                {node.filenames && node.filenames.length > 0 && (
                  <div className="flex items-center gap-1.5 text-gray-700 font-medium">
                    <ChevronRight className="w-4 h-4 text-gray-400" />
                    {node.filenames.join(", ")}
                  </div>
                )}
                {node.job_id && (
                  <div className="text-xs text-gray-400 font-mono">{node.job_id}</div>
                )}
                {node.created_at && (
                  <div className="text-xs text-gray-400">
                    {new Date(node.created_at).toLocaleString("ja-JP")}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Drawer>
    </div>
  );
}

function DetailField({ k, v }: { k: string; v: unknown }) {
  const complex = isComplex(v, k);
  return (
    <div className="px-4 py-2">
      <dt className="text-xs font-medium text-gray-400 mb-0.5">{k}</dt>
      <dd className={`text-sm text-gray-800 break-all ${complex ? "font-mono text-xs" : ""}`}>
        {complex ? (
          <pre className="whitespace-pre-wrap">{formatDetail(v, k)}</pre>
        ) : (
          formatDetail(v, k)
        )}
      </dd>
    </div>
  );
}
