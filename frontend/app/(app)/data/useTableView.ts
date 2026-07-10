"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { classifyColumns, formatCell } from "@/components/ui/format";

export type SortState = { key: string; dir: "asc" | "desc" } | null;

export interface DisplayRow {
  row: Record<string, unknown>;
  index: number; // 元の rows における添字（選択状態の対応に使う）
}

/** null/undefined を末尾に、数値は数値順、それ以外は日本語ロケールで比較する。 */
function compareValues(a: unknown, b: unknown): number {
  const an = a === null || a === undefined;
  const bn = b === null || b === undefined;
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), "ja");
}

function storageKeyFor(spaceId: string | null, viewKey: string | null): string | null {
  return spaceId && viewKey ? `dataview:${spaceId}:${viewKey}` : null;
}

/**
 * データテーブルの探索状態（列の表示/順序・ソート・列フィルタ・全文検索）を束ねるフック。
 * 列設定は localStorage に (spaceId, viewKey) 単位で永続化し、再訪時に復元する。
 * 全行をクライアント保持している前提でメモリ内に導出する（ページングなし）。
 */
export function useTableView(
  rows: Record<string, unknown>[],
  spaceId: string | null,
  viewKey: string | null,
) {
  const { primary, metadata } = useMemo(() => classifyColumns(rows), [rows]);
  const allColumns = useMemo(() => [...primary, ...metadata], [primary, metadata]);

  const [order, setOrder] = useState<string[]>([]);
  const [hidden, setHidden] = useState<string[]>([]);
  const [sort, setSort] = useState<SortState>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [search, setSearch] = useState("");

  const storageKey = storageKeyFor(spaceId, viewKey);

  // ビュー/列構成の変化に応じて列設定を初期化・整合（永続分をマージ）。
  useEffect(() => {
    if (allColumns.length === 0) {
      setOrder([]);
      setHidden([]);
      return;
    }
    let saved: { order?: string[]; hidden?: string[] } = {};
    if (storageKey && typeof window !== "undefined") {
      try {
        saved = JSON.parse(window.localStorage.getItem(storageKey) ?? "{}");
      } catch {
        saved = {};
      }
    }
    const savedOrder = (saved.order ?? []).filter((c) => allColumns.includes(c));
    const appended = allColumns.filter((c) => !savedOrder.includes(c));
    setOrder([...savedOrder, ...appended]);
    setHidden(
      saved.hidden
        ? saved.hidden.filter((c) => allColumns.includes(c))
        : metadata, // 既定でメタデータ列（ID・ベクトル等）は非表示
    );
    // ビュー切替でソート/フィルタ/検索はリセット。
    setSort(null);
    setFilters({});
    setSearch("");
  }, [storageKey, allColumns, metadata]);

  // 列設定を永続化。
  useEffect(() => {
    if (!storageKey || order.length === 0 || typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, JSON.stringify({ order, hidden }));
  }, [storageKey, order, hidden]);

  const visibleColumns = useMemo(
    () => order.filter((c) => !hidden.includes(c)),
    [order, hidden],
  );

  const displayRows = useMemo<DisplayRow[]>(() => {
    let items: DisplayRow[] = rows.map((row, index) => ({ row, index }));

    for (const [key, term] of Object.entries(filters)) {
      const t = term.trim().toLowerCase();
      if (!t) continue;
      items = items.filter(({ row }) => formatCell(row[key], key).toLowerCase().includes(t));
    }

    const q = search.trim().toLowerCase();
    if (q) {
      items = items.filter(({ row }) =>
        visibleColumns.some((c) => formatCell(row[c], c).toLowerCase().includes(q)),
      );
    }

    if (sort) {
      const { key, dir } = sort;
      const factor = dir === "asc" ? 1 : -1;
      items = [...items].sort((a, b) => compareValues(a.row[key], b.row[key]) * factor);
    }

    return items;
  }, [rows, filters, search, sort, visibleColumns]);

  const toggleSort = useCallback((key: string) => {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "asc" };
      if (prev.dir === "asc") return { key, dir: "desc" };
      return null; // desc の次は解除
    });
  }, []);

  const setFilter = useCallback((key: string, term: string) => {
    setFilters((prev) => {
      const next = { ...prev };
      if (term) next[key] = term;
      else delete next[key];
      return next;
    });
  }, []);

  const clearFilters = useCallback(() => {
    setFilters({});
    setSearch("");
  }, []);

  const toggleColumn = useCallback((key: string) => {
    setHidden((prev) => (prev.includes(key) ? prev.filter((c) => c !== key) : [...prev, key]));
  }, []);

  const moveColumn = useCallback((key: string, dir: "up" | "down") => {
    setOrder((prev) => {
      const i = prev.indexOf(key);
      if (i < 0) return prev;
      const j = dir === "up" ? i - 1 : i + 1;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }, []);

  const resetColumns = useCallback(() => {
    if (storageKey && typeof window !== "undefined") {
      window.localStorage.removeItem(storageKey);
    }
    setOrder(allColumns);
    setHidden(metadata);
  }, [storageKey, allColumns, metadata]);

  const filterActive = search.trim().length > 0 || Object.values(filters).some((v) => v.trim());

  return {
    order,
    allColumns,
    metadataColumns: metadata,
    visibleColumns,
    hidden,
    displayRows,
    sort,
    toggleSort,
    filters,
    setFilter,
    clearFilters,
    search,
    setSearch,
    toggleColumn,
    moveColumn,
    resetColumns,
    filterActive,
  };
}
