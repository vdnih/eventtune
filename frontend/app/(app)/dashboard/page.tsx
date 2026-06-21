"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, authFetch, authHeaders } from "@/lib/api";
import { useSpace } from "@/lib/space-context";
import { EmailBlockCard } from "@/components/features/email/EmailBlockCard";
import EventDataPanel from "@/components/features/explorer/EventDataPanel";
import { Loader2, Send, Upload, Wrench, Calendar, RefreshCw, Plus, X, Check, FileText, Trash2, MessageSquare } from "lucide-react";

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

interface ProposedEvent {
  name: string;
  event_date: string | null;
  event_type: string | null;
}

interface FileSuggestion {
  filename: string;
  event_id: string | null;
  event_name: string | null;
  event_date: string | null;
  confidence: number;
  is_new_event: boolean;
  is_multi_event: boolean;
  proposed_events: ProposedEvent[];
}

// 確認モーダル内で共有する「作成予定の新規イベント（ドラフト）」。
// 複数ファイルが同じドラフト id を参照することで1つの新規イベントを共有できる。
interface DraftEvent {
  id: string; // "draft_xxx"（確定後に実 event_id へ置換）
  name: string;
  event_date: string | null;
  event_type: string | null;
}

// ファイルごとの割り当て先 id リスト。各 id はドラフト id か既存 event_id。
// 1件=単一イベント、複数件=複数イベントへ分割。空=AIに委ねる。
// キーはアップロード順のインデックス（文字列）。同名ファイルでも衝突しない。
type FilePlans = Record<string, string[]>;

// アップロードファイルのメタデータと先頭プレビュー（同名ファイルの判別・内容確認用）。
interface FileMeta {
  name: string;
  size: number;
  type: string;
  lastModified: number;
  preview: string; // テキスト系のみ先頭を格納（空文字 = プレビュー非対応）
}

const TEXT_PREVIEW_RE = /\.(csv|tsv|txt|json|md|xml|html?|log|ya?ml)$/i;

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function readFileMeta(file: File): Promise<FileMeta> {
  let preview = "";
  if (TEXT_PREVIEW_RE.test(file.name) || file.type.startsWith("text")) {
    try {
      preview = await file.slice(0, 4096).text();
    } catch {
      preview = "";
    }
  }
  return { name: file.name, size: file.size, type: file.type, lastModified: file.lastModified, preview };
}

function newDraftId(): string {
  return `draft_${crypto.randomUUID().slice(0, 8)}`;
}

// suggestions から共有ドラフト一覧とファイル別割り当てを構築する。
// 同名のドラフトは1つに集約し、AIが同じ新規イベントと判定した複数ファイルを束ねる。
function buildInitialState(suggestions: FileSuggestion[]): {
  draftEvents: DraftEvent[];
  plans: FilePlans;
} {
  const draftEvents: DraftEvent[] = [];
  const draftByName = new Map<string, string>();
  const plans: FilePlans = {};

  function ensureDraft(p: ProposedEvent): string {
    const name = (p.name ?? "").trim();
    if (name) {
      const existing = draftByName.get(name);
      if (existing) return existing;
    }
    const id = newDraftId();
    draftEvents.push({
      id,
      name,
      event_date: p.event_date ?? null,
      event_type: p.event_type ?? null,
    });
    if (name) draftByName.set(name, id);
    return id;
  }

  suggestions.forEach((s, i) => {
    const key = String(i);
    if (!s.is_new_event && !s.is_multi_event && s.event_id) {
      plans[key] = [s.event_id];
      return;
    }
    const proposals: ProposedEvent[] =
      s.proposed_events && s.proposed_events.length > 0
        ? s.proposed_events
        : [{ name: s.event_name ?? "", event_date: s.event_date, event_type: null }];
    plans[key] = proposals.map((p) => ensureDraft(p));
  });
  return { draftEvents, plans };
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
  fileMetas,
  draftEvents,
  plans,
  uploading,
  onEditDraftName,
  onAddDraft,
  onRemoveDraft,
  onSetFileTargets,
  onConfirmUpload,
  onCancelUpload,
}: {
  events: EventSummary[];
  suggestions: FileSuggestion[];
  fileMetas: FileMeta[];
  draftEvents: DraftEvent[];
  plans: FilePlans;
  uploading: boolean;
  onEditDraftName: (draftId: string, name: string) => void;
  onAddDraft: () => string; // 追加したドラフト id を返す
  onRemoveDraft: (draftId: string) => void;
  onSetFileTargets: (key: string, targetIds: string[]) => void;
  onConfirmUpload: () => void;
  onCancelUpload: () => void;
}) {
  // プレビュー展開中のファイル（インデックスキー）
  const [openPreviews, setOpenPreviews] = useState<Set<string>>(new Set());
  function togglePreview(key: string) {
    setOpenPreviews((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  const draftName = (id: string) => draftEvents.find((d) => d.id === id)?.name?.trim() || "（名称未設定）";
  const eventName = (id: string) => {
    const ev = events.find((e) => e.event_id === id);
    return ev ? `${ev.name} (${ev.event_date})` : id;
  };
  const isDraft = (id: string) => id.startsWith("draft_");
  const labelFor = (id: string) => (isDraft(id) ? `🆕 ${draftName(id)}` : eventName(id));

  function targetsOf(key: string): string[] {
    return plans[key] ?? [];
  }
  function addTarget(key: string, id: string) {
    const cur = targetsOf(key);
    if (cur.includes(id)) return;
    onSetFileTargets(key, [...cur, id]);
  }
  function removeTarget(key: string, id: string) {
    onSetFileTargets(key, targetsOf(key).filter((t) => t !== id));
  }
  // ドロップダウンの選択を処理（既存/ドラフト追加、または新規ドラフト作成）
  function onAssignSelect(key: string, value: string) {
    if (!value) return;
    if (value === "__new__") {
      const id = onAddDraft();
      addTarget(key, id);
      return;
    }
    addTarget(key, value);
  }

  function badgeFor(key: string): { badgeClass: string; badgeText: string } {
    const targets = targetsOf(key);
    if (targets.length === 0)
      return { badgeClass: "bg-gray-100 text-gray-500 border border-gray-200", badgeText: "未割り当て" };
    if (targets.length > 1)
      return { badgeClass: "bg-amber-50 text-amber-600 border border-amber-200", badgeText: `⚠️ ${targets.length}件に分割` };
    return isDraft(targets[0])
      ? { badgeClass: "bg-green-50 text-green-600 border border-green-200", badgeText: "🆕 新規" }
      : { badgeClass: "bg-blue-50 text-blue-600 border border-blue-200", badgeText: "既存に紐づけ" };
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-base font-semibold text-gray-900">アップロード確認</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              新規イベントの仮タイトルを編集し、各ファイルをどのイベントに紐づけるか選んでください。
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

        {/* 共有ドラフト新規イベント */}
        <div className="px-6 py-3 bg-gray-50 border-b border-gray-100 space-y-1.5">
          <p className="text-xs font-medium text-gray-600">新規イベント（仮）</p>
          {draftEvents.length === 0 && (
            <p className="text-[11px] text-gray-400">新規イベントはありません。各ファイルで「新規イベントを作成」を選ぶと追加されます。</p>
          )}
          {draftEvents.map((d) => (
            <div key={d.id} className="flex items-center gap-2">
              <span className="text-[11px] shrink-0">🆕</span>
              <input
                type="text"
                value={d.name}
                placeholder="イベント名（仮）"
                onChange={(e) => onEditDraftName(d.id, e.target.value)}
                className="flex-1 text-xs rounded-lg border border-gray-200 px-2.5 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-indigo-300"
              />
              {d.event_date && <span className="text-[11px] text-gray-400 shrink-0">{d.event_date}</span>}
              <button
                onClick={() => onRemoveDraft(d.id)}
                className="p-1 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 transition shrink-0"
                title="この新規イベントを削除"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
          <button
            onClick={() => onAddDraft()}
            className="flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-700 transition pt-0.5"
          >
            <Plus className="w-3 h-3" /> 新規イベントを追加
          </button>
        </div>

        {/* File list */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
          {suggestions.map((s, i) => {
            const key = String(i);
            const meta = fileMetas[i];
            const name = meta?.name ?? s.filename;
            const targets = targetsOf(key);
            const { badgeClass, badgeText } = badgeFor(key);
            const previewOpen = openPreviews.has(key);
            const modified = meta ? new Date(meta.lastModified).toLocaleString("ja-JP", { dateStyle: "short", timeStyle: "short" }) : null;
            return (
              <div key={key} className="p-4 bg-gray-50 rounded-xl border border-gray-100 space-y-2.5">
                <div className="flex items-start gap-3">
                  <span className="text-[11px] font-mono text-gray-400 mt-0.5 shrink-0 w-7">#{i + 1}</span>
                  <FileText className="w-4 h-4 text-gray-400 mt-0.5 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-800 truncate" title={name}>
                      {name}
                    </p>
                    {meta && (
                      <p className="text-[11px] text-gray-400 mt-0.5 truncate">
                        {formatBytes(meta.size)}
                        {meta.type ? ` · ${meta.type}` : ""}
                        {modified ? ` · 更新 ${modified}` : ""}
                      </p>
                    )}
                  </div>
                  <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium shrink-0 ${badgeClass}`}>
                    {badgeText}
                  </span>
                </div>

                {/* プレビュー */}
                <div className="pl-7">
                  <button
                    onClick={() => togglePreview(key)}
                    className="text-[11px] text-indigo-600 hover:text-indigo-700 transition"
                  >
                    {previewOpen ? "▲ プレビューを閉じる" : "▼ 中身をプレビュー"}
                  </button>
                  {previewOpen && (
                    <pre className="mt-1.5 max-h-40 overflow-auto rounded-lg bg-gray-900 text-gray-100 text-[11px] leading-relaxed font-mono p-3 whitespace-pre-wrap break-all">
                      {meta?.preview
                        ? meta.preview.slice(0, 2000)
                        : "このファイル形式はテキストプレビューに対応していません。"}
                    </pre>
                  )}
                </div>

                {/* 割り当て先チップ */}
                <div className="pl-7 flex flex-wrap items-center gap-1.5">
                  {targets.map((id) => (
                    <span
                      key={id}
                      className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg bg-white border border-gray-200 text-gray-700"
                    >
                      <span className="truncate max-w-[200px]" title={labelFor(id)}>{labelFor(id)}</span>
                      <button
                        onClick={() => removeTarget(key, id)}
                        className="text-gray-400 hover:text-red-500 transition"
                        title="割り当てを外す"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                  <select
                    value=""
                    onChange={(e) => onAssignSelect(key, e.target.value)}
                    className="text-[11px] rounded-lg border border-dashed border-gray-300 px-2 py-1 bg-white text-gray-600 focus:outline-none focus:ring-1 focus:ring-indigo-300"
                  >
                    <option value="">＋ イベントを割り当て…</option>
                    <option value="__new__">🆕 新規イベントを作成</option>
                    {draftEvents.length > 0 && (
                      <optgroup label="新規イベント（仮）">
                        {draftEvents
                          .filter((d) => !targets.includes(d.id))
                          .map((d) => (
                            <option key={d.id} value={d.id}>
                              🆕 {d.name.trim() || "（名称未設定）"}
                            </option>
                          ))}
                      </optgroup>
                    )}
                    {events.length > 0 && (
                      <optgroup label="既存イベント">
                        {events
                          .filter((ev) => !targets.includes(ev.event_id))
                          .map((ev) => (
                            <option key={ev.event_id} value={ev.event_id}>
                              {ev.name} ({ev.event_date})
                            </option>
                          ))}
                      </optgroup>
                    )}
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
  onSelectEvent: (id: string | null) => void;
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

      {/* Event list（空白部分クリックで選択解除） */}
      <div
        className="flex-1 overflow-y-auto px-3 pb-3 space-y-1"
        onClick={(e) => { if (e.target === e.currentTarget) onSelectEvent(null); }}
      >
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
            onClick={() => onSelectEvent(selectedEventId === ev.event_id ? null : ev.event_id)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSelectEvent(selectedEventId === ev.event_id ? null : ev.event_id); }}
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
  const { activeSpace } = useSpace();
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(true);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null);
  const [suggestions, setSuggestions] = useState<FileSuggestion[] | null>(null);
  const [plans, setPlans] = useState<FilePlans>({});
  const [draftEvents, setDraftEvents] = useState<DraftEvent[]>([]);
  const [fileMetas, setFileMetas] = useState<FileMeta[]>([]);
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

  // 右ペイン上下分割: 下部チャット領域の高さ（px）。境界ドラッグで調整。
  const [chatHeight, setChatHeight] = useState(360);
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

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
    // アクティブスペース未確定のうちは X-Space-Id を付与できず 422 になるため叩かない
    if (!activeSpace) {
      setEvents([]);
      setLoadingEvents(false);
      return;
    }
    setLoadingEvents(true);
    try {
      const res = await authFetch("/api/events");
      if (res.ok) {
        const data = await res.json();
        const evts: EventSummary[] = data.events ?? [];
        setEvents(evts);
        // 初期状態は未選択（全イベント横断サマリ＋全体チャット）。
        // 選択中イベントが削除されていた場合のみ選択を解除する。
        setSelectedEventId((prev) => (prev && evts.some((e) => e.event_id === prev) ? prev : null));
      }
    } catch {
      // ignore
    } finally {
      setLoadingEvents(false);
    }
  }, [activeSpace]);

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
    // メタデータと先頭プレビューをクライアント側で並列に読み取る（同名ファイルの判別用）
    setFileMetas(await Promise.all(files.map(readFileMeta)));
    try {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      const res = await authFetch("/api/integration/suggest-event", {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error(`エラー ${res.status}`);
      const data = await res.json();
      const list: FileSuggestion[] = (data.suggestions ?? []).map((s: FileSuggestion) => ({
        ...s,
        proposed_events: s.proposed_events ?? [],
      }));
      setSuggestions(list);
      const init = buildInitialState(list);
      setDraftEvents(init.draftEvents);
      setPlans(init.plans);
    } catch {
      // suggest 失敗時はすべて新規イベントとして取り込む
      const fallback: FileSuggestion[] = files.map((f) => ({
        filename: f.name,
        event_id: null,
        event_name: null,
        event_date: null,
        confidence: 0,
        is_new_event: true,
        is_multi_event: false,
        proposed_events: [],
      }));
      setSuggestions(fallback);
      const init = buildInitialState(fallback);
      setDraftEvents(init.draftEvents);
      setPlans(init.plans);
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
      // 参照されているドラフト新規イベントを、ユーザー確定の仮タイトルで先行作成する。
      // ドラフト id は複数ファイルで共有されるため、ここで1度だけ作成すれば
      // 同じ新規イベントに複数ファイルが束ねられる（命名も確定する）。
      const today = new Date().toISOString().slice(0, 10);
      const referenced = new Set(Object.values(plans).flat().filter((id) => id.startsWith("draft_")));
      const draftToReal = new Map<string, string>();
      for (const d of draftEvents) {
        if (!referenced.has(d.id)) continue;
        const name = d.name.trim() || `新規イベント ${today}`;
        const date = d.event_date || today;
        const type = d.event_type || "展示会";
        draftToReal.set(d.id, await createEvent(name, date, type));
      }

      // ファイルの並び順インデックスをキーにする（同名ファイルでも衝突しない）。
      const fileEventMap: Record<string, string[]> = {};
      pendingFiles.forEach((_, i) => {
        const targets = plans[String(i)] ?? [];
        const ids: string[] = [];
        for (const id of targets) {
          const resolved = id.startsWith("draft_") ? draftToReal.get(id) : id;
          if (resolved && !ids.includes(resolved)) ids.push(resolved);
        }
        fileEventMap[String(i)] = ids;
      });

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
      setPlans({});
      setDraftEvents([]);
      setFileMetas([]);
      await fetchEvents();
      pollBatch(batch_id, label);
    } catch (e) {
      replaceLastAssistantMessage(`取り込みに失敗しました: ${(e as Error).message}`);
      setUploading(false);
    }
  }

  function handleCancelUpload() {
    setPendingFiles(null);
    setSuggestions(null);
    setPlans({});
    setDraftEvents([]);
    setFileMetas([]);
  }

  // イベントを1件作成し、生成された event_id を返す（一覧更新・選択は呼び出し側の責務）。
  async function createEvent(name: string, date: string, type: string): Promise<string> {
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
    return newEvent.event_id;
  }

  async function handleCreateEvent(name: string, date: string, type: string) {
    const eventId = await createEvent(name, date, type);
    await fetchEvents();
    setSelectedEventId(eventId);
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
      const res = await fetch(`${API_BASE}/api/marketing/chat`, {
        method: "POST",
        headers: await authHeaders({
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        }),
        body: JSON.stringify({
          message: text,
          session_id: sessionId.current,
          event_id: selectedEventId,
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
    const res = await fetch(`${API_BASE}/api/marketing/runs/${runId}/export`, {
      headers: await authHeaders(),
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

  // ── 右ペイン上下分割のリサイズ ────────────────────────────────────────────

  function handleResizeStart(e: React.PointerEvent) {
    e.preventDefault();
    dragRef.current = { startY: e.clientY, startH: chatHeight };
    const onMove = (ev: PointerEvent) => {
      if (!dragRef.current) return;
      const delta = dragRef.current.startY - ev.clientY;
      const next = Math.min(
        Math.max(dragRef.current.startH + delta, 180),
        window.innerHeight - 220,
      );
      setChatHeight(next);
    };
    const onUp = () => {
      dragRef.current = null;
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
  }

  // ── レンダリング ─────────────────────────────────────────────────────────

  const selectedEvent = events.find((e) => e.event_id === selectedEventId);
  const chatContextLabel = selectedEvent
    ? `${selectedEvent.name}について`
    : "全イベントについて";

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden">
      {/* アップロード確認モーダル */}
      {suggestions !== null && (
        <UploadConfirmModal
          events={events}
          suggestions={suggestions}
          fileMetas={fileMetas}
          draftEvents={draftEvents}
          plans={plans}
          uploading={uploading}
          onEditDraftName={(draftId, name) =>
            setDraftEvents((prev) => prev.map((d) => (d.id === draftId ? { ...d, name } : d)))
          }
          onAddDraft={() => {
            const id = newDraftId();
            setDraftEvents((prev) => [...prev, { id, name: "", event_date: null, event_type: null }]);
            return id;
          }}
          onRemoveDraft={(draftId) => {
            setDraftEvents((prev) => prev.filter((d) => d.id !== draftId));
            setPlans((prev) =>
              Object.fromEntries(
                Object.entries(prev).map(([fn, ids]) => [fn, ids.filter((id) => id !== draftId)])
              )
            );
          }}
          onSetFileTargets={(filename, targetIds) =>
            setPlans((prev) => ({ ...prev, [filename]: targetIds }))
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

      {/* 右パネル: 上=データ確認 / 下=常駐チャット */}
      <div className="flex-1 flex flex-col overflow-hidden bg-white">
        {/* 上: イベントデータパネル（未選択時は全イベント横断サマリ） */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <EventDataPanel selectedEventId={selectedEventId} />
        </div>

        {/* 上下分割のリサイズハンドル */}
        <div
          onPointerDown={handleResizeStart}
          className="shrink-0 h-1.5 cursor-row-resize bg-gray-100 hover:bg-brand-300 transition border-t border-b border-gray-200"
          title="ドラッグで高さを調整"
        />

        {/* 下: 常駐チャット */}
        <div
          className="shrink-0 flex flex-col overflow-hidden bg-white"
          style={{ height: chatHeight }}
        >
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
        <div className="shrink-0 border-t border-gray-100 px-6 py-3">
          {/* チャット文脈インジケータ: 選択中イベント or 全体 */}
          <div className="flex items-center gap-1.5 mb-2 text-xs text-gray-500">
            <MessageSquare className="w-3.5 h-3.5 text-brand-500 shrink-0" />
            <span>
              <span className="font-semibold text-gray-700">{chatContextLabel}</span>
              <span className="text-gray-400">
                {selectedEvent ? " 質問しています" : " 質問しています（左でイベントを選ぶと対象を絞り込めます）"}
              </span>
            </span>
          </div>
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
    </div>
  );
}
