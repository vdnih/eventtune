"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, authFetch, authHeaders } from "@/lib/api";
import { useSpace } from "@/lib/space-context";
import { useThreads } from "@/hooks/useThreads";
import { getThreadMessages } from "@/lib/threads";
import { ThreadSidebar } from "@/components/features/agent/ThreadSidebar";
import { DeliverableCard } from "@/components/features/agent/DeliverableCard";
import { Loader2, Send, Upload, Wrench, X, Check, FileText } from "lucide-react";

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

// AIが生成して実行した Python コードとその結果（利用者が検証できるよう可視化する）
interface CodeBlock {
  code: string;
  output?: string;
  outcome?: string;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCallEvent[];
  codeBlocks?: CodeBlock[];
  deliverables?: DeliverableData[];
  runId?: string;
  loading?: boolean;
}

// 新規チャットの初期表示（新規作成時のリセットにも再利用する）
const INITIAL_MESSAGE: ChatMessage = {
  id: "init",
  role: "assistant",
  content:
    "こんにちは。AIエージェントです。\n\nファイルをアップロードしてデータを取り込むか、チャットで指示をお送りください。\n\n例: 「2025秋の展示会の振り返りをして」「顧客ごとのフォローアップ案を作って」「プロダクトAへの関心が高いリストを分析して」",
};


// 取り込みプラン（POST /api/integration/plan のレスポンス）
interface ProposedLink {
  kind: string;       // "event" | "account" | "product"
  name: string;
  existing: boolean;  // 既存マスタに一致するか
}

interface FilePlan {
  filename: string;
  detected_entity_types: string[];
  proposed_links: ProposedLink[];
  notes: string;
}

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

// ── CodeExecutionPanel ────────────────────────────────────────────────────────
// AIが書いて実行した Python コードと実行結果を表示し、利用者が分析内容を検証できるようにする。

function CodeExecutionPanel({ blocks }: { blocks: CodeBlock[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2 text-xs border border-sky-100 bg-sky-50 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left text-sky-700 hover:bg-sky-100 transition"
      >
        <FileText className="w-3 h-3" />
        <span>AIが実行したコード（{blocks.length}）</span>
        <span className="ml-auto text-sky-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-3 pb-2 space-y-2">
          {blocks.map((b, i) => (
            <div key={i} className="space-y-1">
              <pre className="max-h-60 overflow-auto rounded-lg bg-gray-900 text-gray-100 text-[11px] leading-relaxed font-mono p-3 whitespace-pre-wrap break-all">
                {b.code}
              </pre>
              {(b.output !== undefined) && (
                <pre className="max-h-48 overflow-auto rounded-lg bg-gray-100 text-gray-700 text-[11px] leading-relaxed font-mono p-3 whitespace-pre-wrap break-all border border-gray-200">
                  {b.output ? b.output : "(出力なし)"}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── UploadConfirmModal ────────────────────────────────────────────────────────

const LINK_KIND_LABEL: Record<string, string> = {
  event: "イベント",
  account: "企業",
  product: "製品",
};

function UploadConfirmModal({
  plan,
  fileMetas,
  hint,
  eventName,
  uploading,
  replanning,
  onHintChange,
  onEventNameChange,
  onReplan,
  onConfirmUpload,
  onCancelUpload,
}: {
  plan: FilePlan[];
  fileMetas: FileMeta[];
  hint: string;
  eventName: string;
  uploading: boolean;
  replanning: boolean;
  onHintChange: (value: string) => void;
  onEventNameChange: (value: string) => void;
  onReplan: () => void;
  onConfirmUpload: () => void;
  onCancelUpload: () => void;
}) {
  const [openPreviews, setOpenPreviews] = useState<Set<string>>(new Set());

  function togglePreview(key: string) {
    setOpenPreviews((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  const busy = uploading || replanning;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-base font-semibold text-gray-900">取り込み内容の確認</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              ファイルの内容を解析しました。分解先とリンクを確認し、必要ならチャットで補足してください。
            </p>
          </div>
          <button onClick={onCancelUpload} disabled={busy} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition disabled:opacity-50">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
          {plan.map((fp, i) => {
            const key = String(i);
            const meta = fileMetas[i];
            const name = meta?.name ?? fp.filename;
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
                </div>

                {/* 分解先エンティティ種別 */}
                <div className="pl-7 flex flex-wrap items-center gap-1.5">
                  {fp.detected_entity_types.length === 0 ? (
                    <span className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 border border-gray-200">種別未判定</span>
                  ) : (
                    fp.detected_entity_types.map((t) => (
                      <span key={t} className="text-[11px] px-2 py-0.5 rounded-full font-medium bg-indigo-50 text-indigo-600 border border-indigo-200">{t}</span>
                    ))
                  )}
                </div>

                {/* リンク案 */}
                {fp.proposed_links.length > 0 && (
                  <div className="pl-7 flex flex-wrap items-center gap-1.5">
                    {fp.proposed_links.map((lk, j) => (
                      <span key={j} className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg bg-white border border-gray-200 text-gray-700" title={lk.name}>
                        <span className="text-gray-400">{LINK_KIND_LABEL[lk.kind] ?? lk.kind}:</span>
                        <span className="truncate max-w-[180px]">{lk.name}</span>
                        <span className={lk.existing ? "text-blue-500" : "text-green-600"}>{lk.existing ? "既存" : "新規"}</span>
                      </span>
                    ))}
                  </div>
                )}

                {fp.notes && <p className="pl-7 text-[11px] text-gray-500">💡 {fp.notes}</p>}

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
              </div>
            );
          })}
        </div>

        {/* 既定イベント名（任意・行にイベントリンクが無いとき使う） */}
        <div className="px-6 py-3 bg-gray-50 border-t border-gray-100 space-y-1.5">
          <p className="text-xs font-medium text-gray-600">既定のイベント（任意）</p>
          <input
            type="text"
            value={eventName}
            placeholder="例: 2025秋展示会（参加者リストの行にイベント列が無いとき紐付け先になります）"
            onChange={(e) => onEventNameChange(e.target.value)}
            disabled={busy}
            className="w-full text-xs rounded-lg border border-gray-200 px-2.5 py-2 bg-white focus:outline-none focus:ring-1 focus:ring-indigo-300 disabled:opacity-50"
          />
        </div>

        {/* チャットヒント */}
        <div className="px-6 py-3 bg-gray-50 border-t border-gray-100 space-y-1.5">
          <p className="text-xs font-medium text-gray-600">チャットで補足（任意）</p>
          <div className="flex gap-2">
            <input
              type="text"
              value={hint}
              placeholder="例: これは2025秋展示会の参加者リストです / これは製品マスタです"
              onChange={(e) => onHintChange(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && hint.trim() && !busy) onReplan(); }}
              disabled={busy}
              className="flex-1 text-xs rounded-lg border border-gray-200 px-2.5 py-2 bg-white focus:outline-none focus:ring-1 focus:ring-indigo-300 disabled:opacity-50"
            />
            <button
              onClick={onReplan}
              disabled={busy || !hint.trim()}
              className="flex items-center gap-1 px-3 py-2 text-xs rounded-lg border border-gray-300 text-gray-600 hover:bg-white transition disabled:opacity-40"
            >
              {replanning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
              再解析
            </button>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-gray-200 flex items-center justify-between gap-3">
          <p className="text-xs text-gray-400">{plan.length}件のファイル</p>
          <div className="flex gap-2">
            <button onClick={onCancelUpload} disabled={busy} className="px-4 py-2 text-sm rounded-xl border border-gray-300 text-gray-600 hover:bg-gray-50 transition disabled:opacity-50">
              キャンセル
            </button>
            <button onClick={onConfirmUpload} disabled={busy} className="flex items-center gap-2 px-5 py-2 text-sm rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 transition disabled:opacity-50">
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
  const [plan, setPlan] = useState<FilePlan[] | null>(null);
  const [hint, setHint] = useState("");
  const [eventName, setEventName] = useState("");
  const [fileMetas, setFileMetas] = useState<FileMeta[]>([]);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [replanning, setReplanning] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  // 空文字 = 新規チャット（session_id 未確定）。最初の送信でサーバが採番して確定する。
  const sessionId = useRef<string>("");
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const { threads, reload: reloadThreads, rename: renameThread, remove: removeThread } = useThreads();
  const [messages, setMessages] = useState<ChatMessage[]>([INITIAL_MESSAGE]);
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

  // ── ファイルアップロード（2ステップ: 分解プラン提案 → 確認 → 取り込み）─────────

  async function fetchPlan(files: File[], hintText: string): Promise<FilePlan[]> {
    const formData = new FormData();
    files.forEach((f) => formData.append("files", f));
    if (hintText.trim()) formData.append("hint", hintText.trim());
    const res = await authFetch("/api/integration/plan", { method: "POST", body: formData });
    if (!res.ok) throw new Error(`エラー ${res.status}`);
    const data = await res.json();
    return (data.files ?? []) as FilePlan[];
  }

  async function handleFileSelect(files: File[]) {
    setPendingFiles(files);
    setHint("");
    setEventName("");
    setSuggestLoading(true);
    setFileMetas(await Promise.all(files.map(readFileMeta)));
    try {
      setPlan(await fetchPlan(files, ""));
    } catch {
      // 解析に失敗しても確認画面は出す（ヒント＋取り込みは可能）
      setPlan(files.map((f) => ({
        filename: f.name, detected_entity_types: [], proposed_links: [],
        notes: "内容の自動解析に失敗しました。ヒントで補足するか、そのまま取り込めます。",
      })));
    } finally {
      setSuggestLoading(false);
    }
  }

  // ヒントを反映してプランを再解析する
  async function handleReplan() {
    if (!pendingFiles) return;
    setReplanning(true);
    try {
      setPlan(await fetchPlan(pendingFiles, hint));
    } catch {
      // プレビューは現状維持
    } finally {
      setReplanning(false);
    }
  }

  async function handleConfirmUpload() {
    if (!pendingFiles) return;
    setUploading(true);

    const label =
      pendingFiles.length === 1 ? `「${pendingFiles[0].name}」` : `${pendingFiles.length}件のファイル`;
    addAssistantMessage(`${label}を取り込んでいます...`, [], undefined, true);

    try {
      const formData = new FormData();
      pendingFiles.forEach((f) => formData.append("files", f));
      if (hint.trim()) formData.append("hint", hint.trim());
      if (eventName.trim()) formData.append("event", eventName.trim());

      const res = await authFetch("/api/integration/batches", { method: "POST", body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー ${res.status}`);
      }
      const { batch_id } = await res.json();

      setPendingFiles(null);
      setPlan(null);
      setHint("");
      setEventName("");
      setFileMetas([]);
      pollBatch(batch_id, label);
    } catch (e) {
      replaceLastAssistantMessage(`取り込みに失敗しました: ${(e as Error).message}`);
      setUploading(false);
    }
  }

  function handleCancelUpload() {
    setPendingFiles(null);
    setPlan(null);
    setHint("");
    setEventName("");
    setFileMetas([]);
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
        body: JSON.stringify({ message: text, session_id: sessionId.current || null }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー ${res.status}`);
      }

      const newSessionId = res.headers.get("X-Session-Id");
      if (newSessionId) {
        sessionId.current = newSessionId;
        setActiveThreadId(newSessionId);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error("ストリームを読み込めません");

      const decoder = new TextDecoder();
      let buffer = "";
      let accText = "";
      let toolCalls: ToolCallEvent[] = [];
      let codeBlocks: CodeBlock[] = [];
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
          } else if (event.type === "code") {
            codeBlocks = [...codeBlocks, { code: event.code as string }];
            setMessages((prev) => prev.map((m) => m.id === asstMsgId ? { ...m, codeBlocks, toolCalls, loading: true } : m));
          } else if (event.type === "code_result") {
            // 直近の未完了コードブロックに実行結果を紐づける
            const lastIdx = codeBlocks.map((b) => b.output === undefined).lastIndexOf(true);
            if (lastIdx >= 0) {
              codeBlocks = codeBlocks.map((b, i) =>
                i === lastIdx ? { ...b, output: (event.output as string) ?? "", outcome: event.outcome as string } : b
              );
              setMessages((prev) => prev.map((m) => m.id === asstMsgId ? { ...m, codeBlocks, loading: true } : m));
            }
          } else if (event.type === "text") {
            accText += event.text as string;
            setMessages((prev) => prev.map((m) => m.id === asstMsgId ? { ...m, content: accText, toolCalls, codeBlocks, loading: true } : m));
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
      // タイトル付きスレッドを左ペインに反映（新規作成・updated_at 更新の両方）
      reloadThreads();
    }
  }

  // ── スレッド（会話）切替 ──────────────────────────────────────────────────

  function handleNewChat() {
    if (sending) return;
    sessionId.current = "";
    setActiveThreadId(null);
    setMessages([INITIAL_MESSAGE]);
  }

  async function handleSelectThread(threadId: string) {
    if (sending || threadId === activeThreadId) return;
    const stored = await getThreadMessages(threadId);
    const restored: ChatMessage[] = stored.map((m) => ({
      id: crypto.randomUUID(),
      role: m.role,
      content: m.content,
      toolCalls: m.tool_calls,
      codeBlocks: m.code_blocks,
      runId: m.run_id ?? undefined,
      loading: false,
    }));
    sessionId.current = threadId;
    setActiveThreadId(threadId);
    setMessages(restored.length > 0 ? restored : [INITIAL_MESSAGE]);
    // 成果物（deliverables）は runId から再取得して復元する
    restored.forEach((m) => {
      if (m.runId) loadRunDeliverables(m.id, m.runId);
    });
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

  // activeSpace の変化で新規チャットへリセット（スレッド一覧は useThreads が再ロード）
  const prevSpaceId = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (activeSpace && prevSpaceId.current && prevSpaceId.current !== activeSpace.space_id) {
      sessionId.current = "";
      setActiveThreadId(null);
      setMessages([INITIAL_MESSAGE]);
    }
    prevSpaceId.current = activeSpace?.space_id;
  }, [activeSpace]);

  return (
    <div className="h-[calc(100vh-3.5rem)] flex overflow-hidden bg-white">
      <ThreadSidebar
        threads={threads}
        activeThreadId={activeThreadId}
        onSelect={handleSelectThread}
        onNew={handleNewChat}
        onRename={renameThread}
        onDelete={(id) => {
          removeThread(id);
          if (id === activeThreadId) handleNewChat();
        }}
      />
      <div className="flex-1 flex flex-col overflow-hidden">
      {plan !== null && (
        <UploadConfirmModal
          plan={plan}
          fileMetas={fileMetas}
          hint={hint}
          eventName={eventName}
          uploading={uploading}
          replanning={replanning}
          onHintChange={setHint}
          onEventNameChange={setEventName}
          onReplan={handleReplan}
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

              {msg.role === "assistant" && msg.codeBlocks && msg.codeBlocks.length > 0 && (
                <CodeExecutionPanel blocks={msg.codeBlocks} />
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
    </div>
  );
}
