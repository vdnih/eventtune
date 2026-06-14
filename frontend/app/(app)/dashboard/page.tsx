"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { auth } from "@/lib/firebase";
import { EmailBlockCard } from "@/components/features/email/EmailBlockCard";
import { Loader2, Send, Upload, Wrench, Calendar, RefreshCw, Plus, X, Check, FileText, Trash2 } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function getToken(): Promise<string> {
  return (await auth.currentUser?.getIdToken()) ?? "";
}

async function authFetch(path: string, init?: RequestInit): Promise<Response> {
  const token = await getToken();
  return fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
}

// ── 型定義 ──────────────────────────────────────────────────────────────────

interface ToolCallEvent {
  tool_name: string;
  args: Record<string, unknown>;
}

interface EmailData {
  email_id?: string;
  contact_id?: string;
  lead_id?: string;
  subject: string;
  blocks: {
    block_type: string;
    reason_for_inclusion: string;
    associated_asset_ids?: string[];
    associated_content_ids?: string[];
    block_text: string;
  }[];
  contact_name?: string;
  contact_company?: string;
  engagement_level?: string;
  lead_name?: string;
  lead_company?: string;
  lead_segment?: string;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCallEvent[];
  emails?: EmailData[];
  runId?: string;
  loading?: boolean;
}

interface EventSummary {
  event_id: string;
  name: string;
  event_type: string;
  event_date: string;
  status: string;
}

interface FileSuggestion {
  filename: string;
  event_id: string | null;
  event_name: string | null;
  event_date: string | null;
  confidence: number;
  is_new_event: boolean;
  is_multi_event: boolean;
}

// ── テキスト整形 ─────────────────────────────────────────────────────────────

function renderMarkdown(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code class=\"bg-gray-100 px-1 rounded text-xs font-mono\">$1</code>")
    .replace(/^### (.+)$/gm, "<h3 class=\"font-semibold text-sm mt-2\">$1</h3>")
    .replace(/^## (.+)$/gm, "<h2 class=\"font-semibold text-base mt-3\">$1</h2>")
    .replace(/^# (.+)$/gm, "<h1 class=\"font-bold text-base mt-3\">$1</h1>")
    .replace(/^[-•] (.+)$/gm, "<li class=\"ml-4 list-disc\">$1</li>")
    .replace(/\n/g, "<br/>");
}

// run_id を本文から抽出（run_ + 12桁英数字）
function extractRunId(text: string): string | null {
  const m = text.match(/run_[a-f0-9]{12}/);
  return m ? m[0] : null;
}

// ── ToolCallIndicator ────────────────────────────────────────────────────────

function ToolCallIndicator({ toolCalls }: { toolCalls: ToolCallEvent[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2 text-xs border border-amber-100 bg-amber-50 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left text-amber-700 hover:bg-amber-100 transition"
      >
        <Wrench className="w-3 h-3" />
        <span>{toolCalls.map((t) => t.tool_name).join(", ")}</span>
        <span className="ml-auto text-amber-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-3 pb-2 space-y-1">
          {toolCalls.map((tc, i) => (
            <div key={i} className="font-mono text-[11px] text-amber-800 whitespace-pre-wrap break-all">
              <span className="font-semibold">{tc.tool_name}</span>
              {Object.keys(tc.args).length > 0 && (
                <span className="text-amber-600"> {JSON.stringify(tc.args, null, 2)}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── UploadConfirmModal ────────────────────────────────────────────────────────

function UploadConfirmModal({
  events,
  suggestions,
  overrides,
  uploading,
  onOverrideChange,
  onConfirmUpload,
  onCancelUpload,
}: {
  events: EventSummary[];
  suggestions: FileSuggestion[];
  overrides: Record<string, string | null>;
  uploading: boolean;
  onOverrideChange: (filename: string, eventId: string | null) => void;
  onConfirmUpload: () => void;
  onCancelUpload: () => void;
}) {
  const [bulkEventId, setBulkEventId] = useState("");

  function getEffectiveEventId(s: FileSuggestion): string | null {
    if (overrides[s.filename] !== undefined) return overrides[s.filename];
    if (s.is_new_event || s.is_multi_event) return null;
    return s.event_id;
  }

  function getSuggestionDisplay(s: FileSuggestion): { label: string; badgeClass: string; badgeText: string } {
    const eff = getEffectiveEventId(s);
    if (overrides[s.filename] !== undefined && eff !== null) {
      const ev = events.find((e) => e.event_id === eff);
      return {
        label: ev ? `${ev.name} (${ev.event_date})` : eff,
        badgeClass: "bg-indigo-50 text-indigo-600 border border-indigo-200",
        badgeText: "変更済",
      };
    }
    if (s.is_multi_event) return { label: "複数イベント含む（AI自動振り分け）", badgeClass: "bg-amber-50 text-amber-600 border border-amber-200", badgeText: "⚠️ 複数" };
    if (s.is_new_event || !s.event_name) return { label: "新規イベントとして作成", badgeClass: "bg-green-50 text-green-600 border border-green-200", badgeText: "🆕 新規" };
    return {
      label: `${s.event_name}${s.event_date ? ` (${s.event_date})` : ""}`,
      badgeClass: "bg-blue-50 text-blue-600 border border-blue-200",
      badgeText: `${Math.round(s.confidence * 100)}% 一致`,
    };
  }

  function applyBulkOverride() {
    const eid = bulkEventId === "" ? null : bulkEventId;
    suggestions.forEach((s) => onOverrideChange(s.filename, eid));
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-base font-semibold text-gray-900">アップロード確認</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              AIが各ファイルのイベントを判定しました。内容を確認し、必要に応じて変更してください。
            </p>
          </div>
          <button
            onClick={onCancelUpload}
            disabled={uploading}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition disabled:opacity-50"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Bulk apply */}
        <div className="px-6 py-3 bg-gray-50 border-b border-gray-100 flex items-center gap-3">
          <span className="text-xs text-gray-500 shrink-0">全ファイルに一括適用:</span>
          <select
            value={bulkEventId}
            onChange={(e) => setBulkEventId(e.target.value)}
            className="flex-1 text-xs rounded-lg border border-gray-200 px-2.5 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-indigo-300"
          >
            <option value="">🆕 新規イベントとして作成</option>
            {events.map((ev) => (
              <option key={ev.event_id} value={ev.event_id}>
                {ev.name} ({ev.event_date})
              </option>
            ))}
          </select>
          <button
            onClick={applyBulkOverride}
            className="text-xs px-3 py-1.5 rounded-lg bg-gray-200 hover:bg-gray-300 text-gray-700 transition shrink-0"
          >
            適用
          </button>
        </div>

        {/* File list */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
          {suggestions.map((s) => {
            const { label, badgeClass, badgeText } = getSuggestionDisplay(s);
            const currentVal = getEffectiveEventId(s) ?? "";
            return (
              <div key={s.filename} className="flex items-start gap-4 p-4 bg-gray-50 rounded-xl border border-gray-100">
                <FileText className="w-4 h-4 text-gray-400 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0 space-y-2">
                  <p className="text-sm font-medium text-gray-800 truncate" title={s.filename}>
                    {s.filename}
                  </p>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium shrink-0 ${badgeClass}`}>
                      {badgeText}
                    </span>
                    <span className="text-xs text-gray-500 truncate" title={label}>{label}</span>
                  </div>
                </div>
                <div className="shrink-0 w-56">
                  <select
                    value={currentVal}
                    onChange={(e) =>
                      onOverrideChange(s.filename, e.target.value === "" ? null : e.target.value)
                    }
                    className="w-full text-xs rounded-lg border border-gray-200 px-2.5 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-indigo-300"
                  >
                    <option value="">🆕 新規イベントとして作成</option>
                    {events.map((ev) => (
                      <option key={ev.event_id} value={ev.event_id}>
                        {ev.name} ({ev.event_date})
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-200 flex items-center justify-between gap-3">
          <p className="text-xs text-gray-400">{suggestions.length}件のファイル</p>
          <div className="flex gap-2">
            <button
              onClick={onCancelUpload}
              disabled={uploading}
              className="px-4 py-2 text-sm rounded-xl border border-gray-300 text-gray-600 hover:bg-gray-50 transition disabled:opacity-50"
            >
              キャンセル
            </button>
            <button
              onClick={onConfirmUpload}
              disabled={uploading}
              className="flex items-center gap-2 px-5 py-2 text-sm rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 transition disabled:opacity-50"
            >
              {uploading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Check className="w-4 h-4" />
              )}
              {uploading ? "取り込み中..." : "まとめて取り込む"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── SourcesPanel ─────────────────────────────────────────────────────────────

const TYPE_FILTERS = ["全て", "展示会", "セミナー", "プライベートイベント"] as const;
const TYPE_LABELS: Record<string, string> = {
  "全て": "全て",
  "展示会": "展示会",
  "セミナー": "セミナー",
  "プライベートイベント": "プライベート",
};

function SourcesPanel({
  events,
  loadingEvents,
  selectedEventId,
  onSelectEvent,
  onRefresh,
  pendingFiles,
  suggestLoading,
  uploading,
  onFileSelect,
  onCreateEvent,
  onDelete,
  deletingId,
}: {
  events: EventSummary[];
  loadingEvents: boolean;
  selectedEventId: string | null;
  onSelectEvent: (id: string) => void;
  onRefresh: () => void;
  pendingFiles: File[] | null;
  suggestLoading: boolean;
  uploading: boolean;
  onFileSelect: (files: File[]) => void;
  onCreateEvent: (name: string, date: string, type: string) => Promise<void>;
  onDelete: (eventId: string) => void;
  deletingId: string | null;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [searchText, setSearchText] = useState("");
  const [typeFilter, setTypeFilter] = useState("全て");
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDate, setNewDate] = useState("");
  const [newType, setNewType] = useState("展示会");
  const [creatingEvent, setCreatingEvent] = useState(false);

  const filteredEvents = events
    .filter((ev) => typeFilter === "全て" || ev.event_type === typeFilter)
    .filter(
      (ev) =>
        !searchText ||
        ev.name.toLowerCase().includes(searchText.toLowerCase()) ||
        ev.event_date.includes(searchText)
    );

  async function handleCreateSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!newName || !newDate) return;
    setCreatingEvent(true);
    try {
      await onCreateEvent(newName, newDate, newType);
      setNewName(""); setNewDate(""); setNewType("展示会");
      setShowCreateForm(false);
    } finally {
      setCreatingEvent(false);
    }
  }

  const isLoading = suggestLoading && !!pendingFiles;

  return (
    <aside className="w-64 shrink-0 border-r border-gray-200 bg-gray-50 flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">ソース</span>
        <button
          onClick={onRefresh}
          disabled={loadingEvents}
          className="p-1 rounded hover:bg-gray-200 transition text-gray-400"
          title="更新"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loadingEvents ? "animate-spin" : ""}`} />
        </button>
      </div>

      {/* Upload button */}
      <div className="px-3 py-3 border-b border-gray-100">
        {isLoading ? (
          <div className="flex items-center gap-2 px-3 py-2 text-xs text-indigo-600 bg-indigo-50 rounded-lg">
            <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
            AIがイベントを判別中...
          </div>
        ) : (
          <>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".csv,.xlsx,.xls,.txt,.pdf"
              className="hidden"
              onChange={(e) => {
                const files = Array.from(e.target.files ?? []);
                if (files.length) onFileSelect(files);
                e.target.value = "";
              }}
            />
            <button
              onClick={() => inputRef.current?.click()}
              disabled={uploading}
              className="flex items-center gap-2 w-full rounded-lg border border-dashed border-gray-300 bg-white hover:bg-gray-50 px-3 py-2 text-xs text-gray-500 hover:text-gray-700 transition disabled:opacity-50"
            >
              {uploading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
              ) : (
                <Upload className="w-3.5 h-3.5 shrink-0" />
              )}
              {uploading ? "取り込み中..." : "ファイルを追加（複数可）"}
            </button>
          </>
        )}
      </div>

      {/* Search + filter */}
      <div className="px-3 pt-2 pb-1 space-y-1.5">
        <input
          type="text"
          placeholder="名前・日付で検索..."
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          className="w-full text-xs rounded-lg border border-gray-200 px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-300 bg-white"
        />
        <div className="flex gap-1 flex-wrap">
          {TYPE_FILTERS.map((t) => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium transition ${
                typeFilter === t
                  ? "bg-indigo-100 text-indigo-700"
                  : "bg-gray-100 text-gray-500 hover:bg-gray-200"
              }`}
            >
              {TYPE_LABELS[t]}
            </button>
          ))}
        </div>
      </div>

      {/* Event list */}
      <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-1">
        {loadingEvents && events.length === 0 && (
          <p className="text-xs text-gray-400 px-1 mt-1">読み込み中...</p>
        )}
        {!loadingEvents && filteredEvents.length === 0 && (
          <p className="text-xs text-gray-400 px-1 mt-1">
            {events.length === 0 ? "イベントがありません" : "該当なし"}
          </p>
        )}
        {filteredEvents.map((ev) => (
          <div
            key={ev.event_id}
            role="button"
            tabIndex={0}
            onClick={() => onSelectEvent(ev.event_id)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSelectEvent(ev.event_id); }}
            className={`group w-full text-left rounded-lg border px-3 py-2 transition cursor-pointer ${
              selectedEventId === ev.event_id
                ? "border-indigo-300 bg-indigo-50"
                : "border-gray-100 bg-white hover:border-gray-200 hover:bg-gray-50"
            }`}
          >
            <div className="flex items-start gap-1">
              <p className="flex-1 text-xs font-medium text-gray-800 leading-tight truncate">{ev.name}</p>
              <button
                onClick={(e) => { e.stopPropagation(); onDelete(ev.event_id); }}
                disabled={deletingId === ev.event_id}
                className="shrink-0 p-0.5 rounded text-gray-300 hover:text-red-500 hover:bg-red-50 opacity-0 group-hover:opacity-100 transition disabled:opacity-50"
                title="イベントを削除"
              >
                {deletingId === ev.event_id ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Trash2 className="w-3.5 h-3.5" />
                )}
              </button>
            </div>
            <div className="flex items-center gap-1 mt-0.5">
              <Calendar className="w-3 h-3 text-gray-300 shrink-0" />
              <span className="text-[11px] text-gray-400">{ev.event_date}</span>
              <span
                className={`ml-auto text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                  ev.status === "終了"
                    ? "bg-gray-100 text-gray-500"
                    : ev.status === "開催中"
                    ? "bg-green-100 text-green-600"
                    : "bg-blue-50 text-blue-500"
                }`}
              >
                {ev.status}
              </span>
            </div>
            {ev.event_type && (
              <span className="text-[10px] text-gray-400 mt-0.5 block">{ev.event_type}</span>
            )}
          </div>
        ))}

        {/* Create event */}
        {!showCreateForm ? (
          <button
            onClick={() => setShowCreateForm(true)}
            className="flex items-center gap-1.5 w-full px-3 py-2 text-xs text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition"
          >
            <Plus className="w-3.5 h-3.5" />
            新規イベントを作成
          </button>
        ) : (
          <form
            onSubmit={handleCreateSubmit}
            className="bg-white border border-gray-200 rounded-lg p-2.5 space-y-1.5"
          >
            <input
              type="text"
              placeholder="イベント名"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              required
              className="w-full text-xs rounded border border-gray-200 px-2 py-1 focus:outline-none focus:ring-1 focus:ring-indigo-300"
            />
            <input
              type="date"
              value={newDate}
              onChange={(e) => setNewDate(e.target.value)}
              required
              className="w-full text-xs rounded border border-gray-200 px-2 py-1 focus:outline-none focus:ring-1 focus:ring-indigo-300"
            />
            <select
              value={newType}
              onChange={(e) => setNewType(e.target.value)}
              className="w-full text-xs rounded border border-gray-200 px-2 py-1 bg-white"
            >
              <option value="展示会">展示会</option>
              <option value="セミナー">セミナー</option>
              <option value="プライベートイベント">プライベートイベント</option>
            </select>
            <div className="flex gap-1">
              <button
                type="submit"
                disabled={creatingEvent}
                className="flex-1 text-xs rounded bg-indigo-600 text-white py-1 hover:bg-indigo-700 transition disabled:opacity-50"
              >
                {creatingEvent ? "作成中..." : "作成する"}
              </button>
              <button
                type="button"
                onClick={() => { setShowCreateForm(false); setNewName(""); setNewDate(""); }}
                className="p-1 text-gray-400 hover:text-gray-600"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </form>
        )}
      </div>
    </aside>
  );
}

// ── メインコンポーネント ──────────────────────────────────────────────────────

export default function DashboardPage() {
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(true);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null);
  const [suggestions, setSuggestions] = useState<FileSuggestion[] | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string | null>>({});
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const sessionId = useRef<string>(crypto.randomUUID());
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "init",
      role: "assistant",
      content:
        "こんにちは。イベントマーケティングエージェントです。\n\nファイルをアップロードしてデータを取り込むか、チャットで質問・指示をお送りください。\n\n例: 「2025秋の展示会の振り返りをして」「フォローアップメールを作成して」",
    },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // emails polling
  const pollingRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    return () => {
      Object.values(pollingRefs.current).forEach(clearInterval);
    };
  }, []);

  // ── イベント一覧取得 ──────────────────────────────────────────────────────

  const fetchEvents = useCallback(async () => {
    setLoadingEvents(true);
    try {
      const res = await authFetch("/api/events");
      if (res.ok) {
        const data = await res.json();
        const evts: EventSummary[] = data.events ?? [];
        setEvents(evts);
        // 未選択の場合は最新イベントを自動選択
        setSelectedEventId((prev) => prev ?? evts[0]?.event_id ?? null);
      }
    } catch {
      // ignore
    } finally {
      setLoadingEvents(false);
    }
  }, []);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  // ── イベント削除 ──────────────────────────────────────────────────────────

  const handleDeleteEvent = useCallback(
    async (eventId: string) => {
      const ev = events.find((e) => e.event_id === eventId);
      const name = ev?.name ?? eventId;
      if (!window.confirm(`イベント「${name}」と紐づくデータ（コンタクト・KPI・費用・アンケート）をすべて削除します。元に戻せません。よろしいですか？`)) {
        return;
      }
      setDeletingId(eventId);
      try {
        const res = await authFetch(`/api/events/${eventId}`, { method: "DELETE" });
        if (!res.ok && res.status !== 204) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail ?? `エラー ${res.status}`);
        }
        // 削除したイベントが選択中なら選択を解除する
        setSelectedEventId((prev) => (prev === eventId ? null : prev));
        await fetchEvents();
      } catch (e) {
        window.alert(`削除に失敗しました: ${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setDeletingId(null);
      }
    },
    [events, fetchEvents],
  );

  // ── ファイルアップロード（2ステップ: 提案 → 確認 → 取り込み）──────────────

  async function handleFileSelect(files: File[]) {
    setPendingFiles(files);
    setSuggestLoading(true);
    try {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      const res = await authFetch("/api/integration/suggest-event", {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error(`エラー ${res.status}`);
      const data = await res.json();
      setSuggestions(data.suggestions ?? []);
      setOverrides({});
    } catch {
      // suggest 失敗時はすべて新規イベントとして取り込む
      setSuggestions(
        files.map((f) => ({
          filename: f.name,
          event_id: null,
          event_name: null,
          event_date: null,
          confidence: 0,
          is_new_event: true,
          is_multi_event: false,
        }))
      );
      setOverrides({});
    } finally {
      setSuggestLoading(false);
    }
  }

  async function handleConfirmUpload() {
    if (!pendingFiles || !suggestions) return;
    setUploading(true);

    const label =
      pendingFiles.length === 1
        ? `「${pendingFiles[0].name}」`
        : `${pendingFiles.length}件のファイル`;
    addAssistantMessage(`${label}を取り込んでいます...`, [], undefined, true);

    try {
      const fileEventMap: Record<string, string | null> = {};
      for (const s of suggestions) {
        const override = overrides[s.filename];
        if (override !== undefined) {
          fileEventMap[s.filename] = override;
        } else if (s.is_new_event || s.is_multi_event) {
          fileEventMap[s.filename] = null;
        } else {
          fileEventMap[s.filename] = s.event_id;
        }
      }

      const formData = new FormData();
      pendingFiles.forEach((f) => formData.append("files", f));
      formData.append("file_event_map", JSON.stringify(fileEventMap));

      const res = await authFetch("/api/integration/batches", {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー ${res.status}`);
      }
      const { batch_id } = await res.json();

      setPendingFiles(null);
      setSuggestions(null);
      setOverrides({});
      pollBatch(batch_id, label);
    } catch (e) {
      replaceLastAssistantMessage(`取り込みに失敗しました: ${(e as Error).message}`);
      setUploading(false);
    }
  }

  function handleCancelUpload() {
    setPendingFiles(null);
    setSuggestions(null);
    setOverrides({});
  }

  async function handleCreateEvent(name: string, date: string, type: string) {
    const res = await authFetch("/api/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        event_type: type,
        event_date: date,
        event_date_end: date,
      }),
    });
    if (!res.ok) throw new Error(`イベント作成エラー ${res.status}`);
    const newEvent = await res.json();
    await fetchEvents();
    setSelectedEventId(newEvent.event_id);
  }

  function pollBatch(batchId: string, label: string) {
    const timer = setInterval(async () => {
      try {
        const res = await authFetch(`/api/integration/batches/${batchId}`);
        if (!res.ok) return;
        const data = await res.json();

        // ファイルごとの進捗を ✓/✗/処理中 で表示
        const fileLines = (data.files ?? [])
          .map((f: { filename: string; status: string }) => {
            const icon =
              f.status === "done" ? "✓" : f.status === "error" ? "✗" : "⏳";
            return `${icon} ${f.filename}`;
          })
          .join("\n");

        if (data.status === "done") {
          clearInterval(timer);
          setUploading(false);
          const ce = data.created_entities ?? {};
          const parts = Object.entries(ce)
            .filter(([, v]) => (v as number) > 0)
            .map(([k, v]) => `${k}: ${v}件`);
          replaceLastAssistantMessage(
            `**${label}の取り込みが完了しました。**\n\n` +
              (fileLines ? `${fileLines}\n\n` : "") +
              (data.partial ? "⚠️ 一部のファイルで取り込みに失敗しました。\n\n" : "") +
              (parts.length ? `取り込み結果: ${parts.join(" / ")}\n\n` : "") +
              "「振り返りをして」「メールを作成して」など、何でもお聞かせください。"
          );
          fetchEvents();
        } else if (data.status === "error") {
          clearInterval(timer);
          setUploading(false);
          replaceLastAssistantMessage(
            `取り込みエラー: ${data.error ?? "不明なエラー"}` +
              (fileLines ? `\n\n${fileLines}` : "")
          );
        }
      } catch {
        // keep polling
      }
    }, 2000);
  }

  // ── SSE チャット ─────────────────────────────────────────────────────────

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setSending(true);

    const userMsgId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: "user", content: text },
    ]);

    const asstMsgId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: asstMsgId, role: "assistant", content: "", toolCalls: [], loading: true },
    ]);

    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/marketing/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          Accept: "text/event-stream",
        },
        body: JSON.stringify({
          message: text,
          session_id: sessionId.current,
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー ${res.status}`);
      }

      // セッションIDをレスポンスヘッダーから取得（初回）
      const newSessionId = res.headers.get("X-Session-Id");
      if (newSessionId) sessionId.current = newSessionId;

      const reader = res.body?.getReader();
      if (!reader) throw new Error("ストリームを読み込めません");

      const decoder = new TextDecoder();
      let buffer = "";
      let accText = "";
      let toolCalls: ToolCallEvent[] = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw || raw === "[DONE]") continue;

          let event: { type: string; [key: string]: unknown };
          try {
            event = JSON.parse(raw);
          } catch {
            continue;
          }

          if (event.type === "tool_call") {
            toolCalls = [
              ...toolCalls,
              {
                tool_name: event.tool_name as string,
                args: (event.args ?? {}) as Record<string, unknown>,
              },
            ];
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstMsgId ? { ...m, toolCalls, loading: true } : m
              )
            );
          } else if (event.type === "text") {
            accText += event.text as string;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstMsgId
                  ? { ...m, content: accText, toolCalls, loading: true }
                  : m
              )
            );
          } else if (event.type === "done") {
            const detectedRunId = extractRunId(accText);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstMsgId
                  ? { ...m, content: accText, toolCalls, loading: false, runId: detectedRunId ?? undefined }
                  : m
              )
            );
            if (detectedRunId) {
              startRunPolling(asstMsgId, detectedRunId);
            }
          } else if (event.type === "error") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstMsgId
                  ? {
                      ...m,
                      content: `エラーが発生しました: ${event.message as string}`,
                      loading: false,
                    }
                  : m
              )
            );
          }
        }
      }

      // ストリーム終了後もloading=trueだったら解除
      setMessages((prev) =>
        prev.map((m) =>
          m.id === asstMsgId && m.loading ? { ...m, loading: false } : m
        )
      );
    } catch (e) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === asstMsgId
            ? { ...m, content: `エラー: ${(e as Error).message}`, loading: false }
            : m
        )
      );
    } finally {
      setSending(false);
    }
  }

  // ── メール生成ランのポーリング ────────────────────────────────────────────

  function startRunPolling(msgId: string, runId: string) {
    if (pollingRefs.current[runId]) return;

    // fire-and-forget: trigger actual email generation via BackgroundTask
    // 400 means already running/done — safe to ignore
    authFetch(`/api/marketing/runs/${runId}/execute`, { method: "POST" })
      .then((res) => {
        if (!res.ok && res.status !== 400) {
          console.warn(`execute endpoint returned ${res.status} for run ${runId}`);
        }
      })
      .catch((e) => console.warn("failed to call execute endpoint:", e));

    const timer = setInterval(async () => {
      try {
        const res = await authFetch(`/api/marketing/runs/${runId}`);
        if (!res.ok) return;
        const data = await res.json();

        if (data.status === "done") {
          clearInterval(timer);
          delete pollingRefs.current[runId];
          await loadRunEmails(msgId, runId);
        } else if (data.status === "error") {
          clearInterval(timer);
          delete pollingRefs.current[runId];
        }
      } catch {
        // keep polling
      }
    }, 2000);

    pollingRefs.current[runId] = timer;
  }

  async function loadRunEmails(msgId: string, runId: string) {
    try {
      const res = await authFetch(`/api/marketing/runs/${runId}/results`);
      if (!res.ok) return;
      const data = await res.json();
      const emails: EmailData[] = data.emails ?? [];
      setMessages((prev) =>
        prev.map((m) => (m.id === msgId ? { ...m, emails } : m))
      );
    } catch {
      // ignore
    }
  }

  async function handleDownloadCsv(runId: string) {
    const token = await getToken();
    const res = await fetch(`${API_BASE}/api/marketing/runs/${runId}/export`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `emails_${runId.slice(0, 8)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ── メッセージ操作ヘルパー ────────────────────────────────────────────────

  function addAssistantMessage(
    content: string,
    toolCalls: ToolCallEvent[] = [],
    runId?: string,
    loading = false
  ) {
    const id = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id, role: "assistant", content, toolCalls, runId, loading },
    ]);
    return id;
  }

  function replaceLastAssistantMessage(content: string) {
    setMessages((prev) => {
      const next = [...prev];
      const idx = next.findLastIndex((m) => m.role === "assistant");
      if (idx >= 0) next[idx] = { ...next[idx], content, loading: false };
      return next;
    });
  }

  // ── レンダリング ─────────────────────────────────────────────────────────

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden">
      {/* アップロード確認モーダル */}
      {suggestions !== null && (
        <UploadConfirmModal
          events={events}
          suggestions={suggestions}
          overrides={overrides}
          uploading={uploading}
          onOverrideChange={(filename, eventId) =>
            setOverrides((prev) => ({ ...prev, [filename]: eventId }))
          }
          onConfirmUpload={handleConfirmUpload}
          onCancelUpload={handleCancelUpload}
        />
      )}

      {/* 左パネル: Sources */}
      <SourcesPanel
        events={events}
        loadingEvents={loadingEvents}
        selectedEventId={selectedEventId}
        onSelectEvent={setSelectedEventId}
        onRefresh={fetchEvents}
        pendingFiles={pendingFiles}
        suggestLoading={suggestLoading}
        uploading={uploading}
        onFileSelect={handleFileSelect}
        onCreateEvent={handleCreateEvent}
        onDelete={handleDeleteEvent}
        deletingId={deletingId}
      />

      {/* 右パネル: チャット */}
      <div className="flex-1 flex flex-col overflow-hidden bg-white">
        {/* メッセージ履歴 */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "bg-brand-600 text-white rounded-br-sm"
                    : "bg-gray-50 border border-gray-200 text-gray-700 rounded-bl-sm"
                }`}
              >
                {/* ツールコール表示 */}
                {msg.role === "assistant" &&
                  msg.toolCalls &&
                  msg.toolCalls.length > 0 && (
                    <ToolCallIndicator toolCalls={msg.toolCalls} />
                  )}

                {/* テキスト本文 */}
                {msg.loading && !msg.content && (
                  <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                )}
                {msg.content && (
                  <div
                    className={msg.role === "assistant" ? "mt-1" : ""}
                    dangerouslySetInnerHTML={{
                      __html: renderMarkdown(msg.content),
                    }}
                  />
                )}
                {msg.loading && msg.content && (
                  <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-gray-400 animate-pulse rounded-sm align-middle" />
                )}

                {/* メール結果 */}
                {msg.emails && msg.emails.length > 0 && (
                  <div className="mt-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold text-gray-500">
                        生成済みメール ({msg.emails.length}件)
                      </span>
                      {msg.runId && (
                        <button
                          onClick={() => handleDownloadCsv(msg.runId!)}
                          className="text-xs text-brand-600 hover:text-brand-800 underline"
                        >
                          CSVダウンロード
                        </button>
                      )}
                    </div>
                    {msg.emails.map((email, j) => (
                      <EmailBlockCard key={j} email={email} index={j} />
                    ))}
                  </div>
                )}

                {/* メール生成中インジケーター */}
                {msg.runId && !msg.emails && !msg.loading && (
                  <div className="mt-2 flex items-center gap-2 text-xs text-gray-400">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    メールを生成しています...
                  </div>
                )}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* 入力エリア */}
        <div className="shrink-0 border-t border-gray-100 px-6 py-4">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
            className="flex gap-3"
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="指示を入力してください（例: 2025秋の展示会の振り返りをして）"
              disabled={sending}
              className="flex-1 rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 disabled:bg-gray-50 disabled:text-gray-400"
            />
            <button
              type="submit"
              disabled={!input.trim() || sending}
              className="flex items-center justify-center w-10 h-10 bg-brand-600 text-white rounded-xl hover:bg-brand-700 transition disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
            >
              {sending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
