"use client";

/**
 * SpaceProvider / useSpace — アクティブスペースの状態管理
 *
 * 所属スペース一覧・アクティブスペース・role を保持する。アクティブスペースIDは
 * lib/api.ts の setActiveSpaceId にも反映し、authFetch が X-Space-Id を付与できるようにする。
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { authFetch, getActiveSpaceId, setActiveSpaceId } from "@/lib/api";

export interface SpaceSummary {
  space_id: string;
  name: string;
  role: string;
}

interface SpaceContextValue {
  spaces: SpaceSummary[];
  activeSpace: SpaceSummary | null;
  loading: boolean;
  isOwner: boolean;
  switchSpace: (spaceId: string) => void;
  reloadSpaces: () => Promise<SpaceSummary[]>;
}

const SpaceCtx = createContext<SpaceContextValue | null>(null);

export function SpaceProvider({ children }: { children: ReactNode }) {
  const [spaces, setSpaces] = useState<SpaceSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(getActiveSpaceId());
  const [loading, setLoading] = useState(true);

  const reloadSpaces = useCallback(async (): Promise<SpaceSummary[]> => {
    setLoading(true);
    try {
      const res = await authFetch("/api/spaces");
      if (!res.ok) return [];
      const data = await res.json();
      const list: SpaceSummary[] = data.spaces ?? [];
      setSpaces(list);

      // アクティブスペースの整合性を取る: 未設定 or 所属外なら先頭を採用
      setActiveId((prev) => {
        const valid = prev && list.some((s) => s.space_id === prev);
        const next = valid ? prev : list[0]?.space_id ?? null;
        setActiveSpaceId(next);
        return next;
      });
      return list;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reloadSpaces();
  }, [reloadSpaces]);

  const switchSpace = useCallback((spaceId: string) => {
    setActiveSpaceId(spaceId);
    setActiveId(spaceId);
  }, []);

  const activeSpace = spaces.find((s) => s.space_id === activeId) ?? null;

  return (
    <SpaceCtx.Provider
      value={{
        spaces,
        activeSpace,
        loading,
        isOwner: activeSpace?.role === "owner",
        switchSpace,
        reloadSpaces,
      }}
    >
      {children}
    </SpaceCtx.Provider>
  );
}

export function useSpace(): SpaceContextValue {
  const ctx = useContext(SpaceCtx);
  if (!ctx) throw new Error("useSpace must be used within SpaceProvider");
  return ctx;
}
