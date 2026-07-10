"use client";

/**
 * ThreadSidebar — チャットの左ペイン（会話一覧）
 *
 * 一般的な AI チャットアプリ同様、上部に「新規チャット」、下に過去スレッドを
 * updated_at 降順で並べる。各行はホバーでリネーム／削除メニューを出す。
 */
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { MessageSquarePlus, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import type { ThreadSummary } from "@/lib/threads";

interface ThreadSidebarProps {
  threads: ThreadSummary[];
  activeThreadId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

export function ThreadSidebar({
  threads,
  activeThreadId,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: ThreadSidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  function startRename(t: ThreadSummary) {
    setEditingId(t.thread_id);
    setEditValue(t.title);
  }

  function commitRename() {
    if (editingId) {
      const title = editValue.trim();
      if (title) onRename(editingId, title);
    }
    setEditingId(null);
  }

  return (
    <aside className="w-64 shrink-0 border-r border-gray-200 bg-gray-50 flex flex-col">
      <div className="p-3">
        <button
          onClick={onNew}
          className="w-full flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 transition"
        >
          <MessageSquarePlus className="w-4 h-4" /> 新規チャット
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
        {threads.length === 0 && (
          <p className="px-3 py-6 text-xs text-gray-400 text-center">
            まだ会話がありません。
          </p>
        )}
        {threads.map((t) => {
          const active = t.thread_id === activeThreadId;
          if (editingId === t.thread_id) {
            return (
              <input
                key={t.thread_id}
                autoFocus
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onBlur={commitRename}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename();
                  if (e.key === "Escape") setEditingId(null);
                }}
                className="w-full px-3 py-2 text-sm rounded-lg border border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-400"
              />
            );
          }
          return (
            <ThreadRow
              key={t.thread_id}
              thread={t}
              active={active}
              onSelect={() => onSelect(t.thread_id)}
              onRename={() => startRename(t)}
              onDelete={() => {
                if (window.confirm(`「${t.title}」を削除しますか？`)) onDelete(t.thread_id);
              }}
            />
          );
        })}
      </div>
    </aside>
  );
}

function ThreadRow({
  thread,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  thread: ThreadSummary;
  active: boolean;
  onSelect: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  return (
    <div
      ref={ref}
      className={cn(
        "group relative flex items-center rounded-lg",
        active ? "bg-brand-50" : "hover:bg-gray-100",
      )}
    >
      <button
        onClick={onSelect}
        className={cn(
          "flex-1 min-w-0 flex items-center gap-1.5 text-left px-3 py-2 text-sm truncate",
          active ? "text-brand-700 font-medium" : "text-gray-700",
        )}
        title={thread.title}
      >
        <span className="truncate">{thread.title}</span>
      </button>
      <button
        onClick={() => setMenuOpen((v) => !v)}
        className={cn(
          "shrink-0 p-1.5 mr-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-200",
          menuOpen ? "opacity-100" : "opacity-0 group-hover:opacity-100",
        )}
        title="メニュー"
      >
        <MoreHorizontal className="w-4 h-4" />
      </button>
      {menuOpen && (
        <div className="absolute right-1 top-9 z-20 w-32 bg-white border border-gray-200 rounded-md shadow-lg py-1">
          <button
            onClick={() => {
              setMenuOpen(false);
              onRename();
            }}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
          >
            <Pencil className="w-3.5 h-3.5 text-gray-400" /> 名前を変更
          </button>
          <button
            onClick={() => {
              setMenuOpen(false);
              onDelete();
            }}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50"
          >
            <Trash2 className="w-3.5 h-3.5" /> 削除
          </button>
        </div>
      )}
    </div>
  );
}
