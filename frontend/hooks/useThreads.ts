"use client";

/**
 * useThreads — スレッド一覧の状態管理
 *
 * space-context.tsx の Context+reload 方針に倣う。アクティブスペースの変化で再ロードし、
 * リネーム・削除はローカル state を楽観更新する。
 */
import { useCallback, useEffect, useState } from "react";
import { useSpace } from "@/lib/space-context";
import {
  deleteThread,
  listThreads,
  renameThread,
  type ThreadSummary,
} from "@/lib/threads";

export function useThreads() {
  const { activeSpace } = useSpace();
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setThreads(await listThreads());
    } finally {
      setLoading(false);
    }
  }, []);

  // アクティブスペースが変わったら一覧を取り直す
  useEffect(() => {
    if (activeSpace) reload();
    else setThreads([]);
  }, [activeSpace?.space_id, reload]);

  const rename = useCallback(async (id: string, title: string) => {
    setThreads((prev) => prev.map((t) => (t.thread_id === id ? { ...t, title } : t)));
    await renameThread(id, title);
  }, []);

  const remove = useCallback(async (id: string) => {
    setThreads((prev) => prev.filter((t) => t.thread_id !== id));
    await deleteThread(id);
  }, []);

  return { threads, loading, reload, rename, remove };
}
