"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, authFetch, authHeaders } from "@/lib/api";
import { useSpace } from "@/lib/space-context";
import { DeliverableCard } from "@/components/features/agent/DeliverableCard";
import { Loader2, Send, Upload, Wrench, X, Check, FileText, Trash2, Plus } from "lucide-react";

// ── 型定義 ──────────────────────────────────────────────────────────────────

interface ToolCallEvent {
  tool_name: string;
  args: Record<string, unknown>;
}

interface DeliverableData {
  deliverable_id?: string;
  email_id?: string;       // 旧フィールド名との互換
  person_id?: string;
  contact_id?: string;     // 旧フィールド名との互換
  subject?: string;
  blocks: {
    block_type: string;
    reason_for_inclusion: string;
    associated_asset_ids?: string[];
    associated_content_ids?: string[];
    block_text?: string;
  }[];
  person_name?: string;
  person_company?: string;
  bucket?: string;
  contact_name?: string;       // 旧フィールド名との互換
  contact_company?: string;    // 旧フィールド名との互換
  engagement_level?: string;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCallEvent[];
  deliverables?: DeliverableData[];
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

interface DraftEvent {
  id: string;
  name: string;
  event_date: string | null;
  event_type: string | null;
}

type FilePlans = Record<string, string[]>;

interface FileMeta {
  name: string;
  size: number;
  type: string;
  lastModified: number;
  preview: string;
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
    draftEvents.push({ id, name, event_date: p.event_date ?? null, event_type: p.event_type ?? null });
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
  suggestions: FileSuggestion[];
  fileMetas: FileMeta[];
  draftEvents: DraftEvent[];
  plans: FilePlans;
  uploading: boolean;
  onEditDraftName: (draftId: string, name: string) => void;
  onAddDraft: () => string;
  onRemoveDraft: (draftId: string) => void;
  onSetFileTargets: (key: string, targetIds: string[]) => void;
  onConfirmUpload: () => void;
  onCancelUpload: () => void;
}) {
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [openPreviews, setOpenPreviews] = useState<Set<string>>(new Set());

  useEffect(() => {
    authFetch("/api/events")
      .then((r) => r.json())
      .then((d) => setEvents(d.events ?? []))
      .catch(() => setEvents([]));
  }, []);

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
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-base font-semibold text-gray-900">アップロード確認</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              新規イベントの仮タイトルを編集し、各ファイルをどのイベントに紐づけるか選んでください。
            </p>
          </div>
          <button onClick={onCancelUpload} disabled={uploading} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition disabled:opacity-50">
            <X className="w-4 h-4" />
          </button>
        </div>

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
              <button onClick={() => onRemoveDraft(d.id)} className="p-1 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 transition shrink-0" title="この新規イベントを削除">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
          <button onClick={() => onAddDraft()} className="flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-700 transition pt-0.5">
            <Plus className="w-3 h-3" /> 新規イベントを追加
          </button>
        </div>

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
                    <p className="text-sm font-medium text-gray-800 truncate" title={name}>{name}</p>
                    {meta && (
                      <p className="text-[11px] text-gray-400 mt-0.5 truncate">
                        {formatBytes(meta.size)}
                        {meta.type ? ` · ${meta.type}` : ""}
                        {modified ? ` · 更新 ${modified}` : ""}
                      </p>
                    )}
                  </div>
                  <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium shrink-0 ${badgeClass}`}>{badgeText}</span>
                </div>

                <div className="pl-7">
                  <button onClick={() => togglePreview(key)} className="text-[11px] text-indigo-600 hover:text-indigo-700 transition">
                    {previewOpen ? "▲ プレビューを閉じる" : "▼ 中身をプレビュー"}
                  </button>
                  {previewOpen && (
                    <pre className="mt-1.5 max-h-40 overflow-auto rounded-lg bg-gray-900 text-gray-100 text-[11px] leading-relaxed font-mono p-3 whitespace-pre-wrap break-all">
                      {meta?.preview ? meta.preview.slice(0, 2000) : "このファイル形式はテキストプレビューに対応していません。"}
                    </pre>
                  )}
                </div>

                <div className="pl-7 flex flex-wrap items-center gap-1.5">
                  {targets.map((id) => (
                    <span key={id} className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg bg-white border border-gray-200 text-gray-700">
                      <span className="truncate max-w-[200px]" title={labelFor(id)}>{labelFor(id)}</span>
                      <button onClick={() => removeTarget(key, id)} className="text-gray-400 hover:text-red-500 transition" title="割り当てを外す">
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
                        {draftEvents.filter((d) => !targets.includes(d.id)).map((d) => (
                          <option key={d.id} value={d.id}>🆕 {d.name.trim() || "（名称未設定）"}</option>
                        ))}
                      </optgroup>
                    )}
                    {events.length > 0 && (
                      <optgroup label="既存イベント">
                        {events.filter((ev) => !targets.includes(ev.event_id)).map((ev) => (
                          <option key={ev.event_id} value={ev.event_id}>{ev.name} ({ev.event_date})</option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                </div>
              </div>
            );
          })}
        </div>

        <div className="px-6 py-4 border-t border-gray-200 flex items-center justify-between gap-3">
          <p className="text-xs text-gray-400">{suggestions.length}件のファイル</p>
          <div className="flex gap-2">
            <button onClick={onCancelUpload} disabled={uploading} className="px-4 py-2 text-sm rounded-xl border border-gray-300 text-gray-600 hover:bg-gray-50 transition disabled:opacity-50">
              キャンセル
            </button>
            <button onClick={onConfirmUpload} disabled={uploading} className="flex items-center gap-2 px-5 py-2 text-sm rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 transition disabled:opacity-50">
              {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              {uploading ? "取り込み中..." : "まとめて取り込む"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── メインコンポーネント ──────────────────────────────────────────────────────

export default function DashboardPage() {
  const { activeSpace } = useSpace();
  const [uploading, setUploading] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null);
  const [suggestions, setSuggestions] = useState<FileSuggestion[] | null>(null);
  const [plans, setPlans] = useState<FilePlans>({});
  const [draftEvents, setDraftEvents] = useState<DraftEvent[]>([]);
  const [fileMetas, setFileMetas] = useState<FileMeta[]>([]);
  const [suggestLoading, setSuggestLoading] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const sessionId = useRef<string>(crypto.randomUUID());
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "init",
      role: "assistant",
      content:
        "こんにちは。AIエージェントです。\n\nファイルをアップロードしてデータを取り込むか、チャットで指示をお送りください。\n\n例: 「2025秋の展示会の振り返りをして」「顧客ごとのフォローアップ案を作って」「プロダクトAへの関心が高いリストを分析して」",
    },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const pollingRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    return () => {
      Object.values(pollingRefs.current).forEach(clearInterval);
    };
  }, []);

  // ── ファイルアップロード（2ステップ: 提案 → 確認 → 取り込み）──────────────

  async function handleFileSelect(files: File[]) {
    setPendingFiles(files);
    setSuggestLoading(true);
    setFileMetas(await Promise.all(files.map(readFileMeta)));
    try {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      const res = await authFetch("/api/integration/suggest-event", { method: "POST", body: formData });
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
      const fallback: FileSuggestion[] = files.map((f) => ({
        filename: f.name, event_id: null, event_name: null, event_date: null,
        confidence: 0, is_new_event: true, is_multi_event: false, proposed_events: [],
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
      pendingFiles.length === 1 ? `「${pendingFiles[0].name}」` : `${pendingFiles.length}件のファイル`;
    addAssistantMessage(`${label}を取り込んでいます...`, [], undefined, true);

    try {
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

      const res = await authFetch("/api/integration/batches", { method: "POST", body: formData });
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

  async function createEvent(name: string, date: string, type: string): Promise<string> {
    const res = await authFetch("/api/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, event_type: type, event_date: date, event_date_end: date }),
    });
    if (!res.ok) throw new Error(`イベント作成エラー ${res.status}`);
    const newEvent = await res.json();
    return newEvent.event_id;
  }

  function pollBatch(batchId: string, label: string) {
    const timer = setInterval(async () => {
      try {
        const res = await authFetch(`/api/integration/batches/${batchId}`);
        if (!res.ok) return;
        const data = await res.json();

        const fileLines = (data.files ?? [])
          .map((f: { filename: string; status: string }) => {
            const icon = f.status === "done" ? "✓" : f.status === "error" ? "✗" : "⏳";
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
              "データの確認は「データ」タブから行えます。"
          );
        } else if (data.status === "error") {
          clearInterval(timer);
          setUploading(false);
          replaceLastAssistantMessage(`取り込みエラー: ${data.error ?? "不明なエラー"}` + (fileLines ? `\n\n${fileLines}` : ""));
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
    setMessages((prev) => [...prev, { id: userMsgId, role: "user", content: text }]);

    const asstMsgId = crypto.randomUUID();
    setMessages((prev) => [...prev, { id: asstMsgId, role: "assistant", content: "", toolCalls: [], loading: true }]);

    try {
      const res = await fetch(`${API_BASE}/api/marketing/chat`, {
        method: "POST",
        headers: await authHeaders({ "Content-Type": "application/json", Accept: "text/event-stream" }),
        body: JSON.stringify({ message: text, session_id: sessionId.current }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー ${res.status}`);
      }

      const newSessionId = res.headers.get("X-Session-Id");
      if (newSessionId) sessionId.current = newSessionId;

      const reader = res.body?.getReader();
      if (!reader) throw new Error("ストリームを読み込めません");

      const decoder = new TextDecoder();
      let buffer = "";
      let accText = "";
      let toolCalls: ToolCallEvent[] = [];
      let toolRunId: string | null = null;

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
            toolCalls = [...toolCalls, { tool_name: event.tool_name as string, args: (event.args ?? {}) as Record<string, unknown> }];
            setMessages((prev) => prev.map((m) => m.id === asstMsgId ? { ...m, toolCalls, loading: true } : m));
          } else if (event.type === "tool_result") {
            const result = (event.result ?? {}) as Record<string, unknown>;
            const inner = (result.result ?? result) as Record<string, unknown>;
            let parsed: Record<string, unknown> = inner;
            if (typeof inner === "string") {
              try { parsed = JSON.parse(inner); } catch { parsed = {}; }
            }
            if (event.tool_name === "run_assembly" && typeof parsed.run_id === "string") {
              toolRunId = parsed.run_id as string;
            }
          } else if (event.type === "text") {
            accText += event.text as string;
            setMessages((prev) => prev.map((m) => m.id === asstMsgId ? { ...m, content: accText, toolCalls, loading: true } : m));
          } else if (event.type === "done") {
            const detectedRunId = toolRunId ?? extractRunId(accText);
            setMessages((prev) => prev.map((m) =>
              m.id === asstMsgId ? { ...m, content: accText, toolCalls, loading: false, runId: detectedRunId ?? undefined } : m
            ));
            if (detectedRunId) startRunPolling(asstMsgId, detectedRunId);
          } else if (event.type === "error") {
            setMessages((prev) => prev.map((m) =>
              m.id === asstMsgId ? { ...m, content: `エラーが発生しました: ${event.message as string}`, loading: false } : m
            ));
          }
        }
      }

      setMessages((prev) => prev.map((m) => m.id === asstMsgId && m.loading ? { ...m, loading: false } : m));
    } catch (e) {
      setMessages((prev) => prev.map((m) =>
        m.id === asstMsgId ? { ...m, content: `エラー: ${(e as Error).message}`, loading: false } : m
      ));
    } finally {
      setSending(false);
    }
  }

  // ── 成果物ポーリング ──────────────────────────────────────────────────────

  function startRunPolling(msgId: string, runId: string) {
    if (pollingRefs.current[runId]) return;
    const timer = setInterval(async () => {
      try {
        const res = await authFetch(`/api/marketing/runs/${runId}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.status === "done") {
          clearInterval(timer);
          delete pollingRefs.current[runId];
          await loadRunDeliverables(msgId, runId);
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

  async function loadRunDeliverables(msgId: string, runId: string) {
    try {
      const res = await authFetch(`/api/marketing/runs/${runId}/results`);
      if (!res.ok) return;
      const data = await res.json();
      const deliverables: DeliverableData[] = data.emails ?? data.deliverables ?? [];
      setMessages((prev) => prev.map((m) => m.id === msgId ? { ...m, deliverables } : m));
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
    a.download = `deliverables_${runId.slice(0, 8)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ── メッセージ操作ヘルパー ────────────────────────────────────────────────

  function addAssistantMessage(content: string, toolCalls: ToolCallEvent[] = [], runId?: string, loading = false) {
    const id = crypto.randomUUID();
    setMessages((prev) => [...prev, { id, role: "assistant", content, toolCalls, runId, loading }]);
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

  // activeSpace の変化でセッションをリセット
  const prevSpaceId = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (activeSpace && prevSpaceId.current && prevSpaceId.current !== activeSpace.space_id) {
      sessionId.current = crypto.randomUUID();
    }
    prevSpaceId.current = activeSpace?.space_id;
  }, [activeSpace]);

  return (
    <div className="h-[calc(100vh-3.5rem)] flex flex-col overflow-hidden bg-white">
      {suggestions !== null && (
        <UploadConfirmModal
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
              Object.fromEntries(Object.entries(prev).map(([fn, ids]) => [fn, ids.filter((id) => id !== draftId)]))
            );
          }}
          onSetFileTargets={(filename, targetIds) => setPlans((prev) => ({ ...prev, [filename]: targetIds }))}
          onConfirmUpload={handleConfirmUpload}
          onCancelUpload={handleCancelUpload}
        />
      )}

      {/* メッセージ履歴 */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                msg.role === "user"
                  ? "bg-brand-600 text-white rounded-br-sm"
                  : "bg-gray-50 border border-gray-200 text-gray-700 rounded-bl-sm"
              }`}
            >
              {msg.role === "assistant" && msg.toolCalls && msg.toolCalls.length > 0 && (
                <ToolCallIndicator toolCalls={msg.toolCalls} />
              )}

              {msg.loading && !msg.content && <Loader2 className="w-4 h-4 animate-spin text-gray-400" />}
              {msg.content && (
                <div
                  className={msg.role === "assistant" ? "mt-1" : ""}
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                />
              )}
              {msg.loading && msg.content && (
                <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-gray-400 animate-pulse rounded-sm align-middle" />
              )}

              {/* 成果物 */}
              {msg.deliverables && msg.deliverables.length > 0 && (
                <div className="mt-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-gray-500">
                      AIの成果物 ({msg.deliverables.length}件)
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
                  {msg.deliverables.map((d, j) => (
                    <DeliverableCard key={j} deliverable={d} index={j} />
                  ))}
                </div>
              )}

              {msg.runId && !msg.deliverables && !msg.loading && (
                <div className="mt-2 flex items-center gap-2 text-xs text-gray-400">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  成果物を生成しています...
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* 入力エリア */}
      <div className="shrink-0 border-t border-gray-100 px-6 py-3">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".csv,.xlsx,.xls,.txt,.pdf"
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) handleFileSelect(files);
            e.target.value = "";
          }}
        />
        <form
          onSubmit={(e) => { e.preventDefault(); handleSend(); }}
          className="flex gap-2"
        >
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading || !!suggestLoading}
            className="flex items-center justify-center w-10 h-10 rounded-xl border border-gray-200 text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition disabled:opacity-40 shrink-0"
            title="ファイルをアップロード"
          >
            {suggestLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
          </button>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="指示を入力してください（例: 顧客ごとのフォローアップ案を作って）"
            disabled={sending}
            className="flex-1 rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 disabled:bg-gray-50 disabled:text-gray-400"
          />
          <button
            type="submit"
            disabled={!input.trim() || sending}
            className="flex items-center justify-center w-10 h-10 bg-brand-600 text-white rounded-xl hover:bg-brand-700 transition disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
          >
            {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </form>
      </div>
    </div>
  );
}
