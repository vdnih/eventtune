"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, authFetch, authHeaders } from "@/lib/api";
import { useSpace } from "@/lib/space-context";
import { useThreads } from "@/hooks/useThreads";
import { getThreadMessages } from "@/lib/threads";
import { ThreadSidebar } from "@/components/features/agent/ThreadSidebar";
import { DeliverableCard } from "@/components/features/agent/DeliverableCard";
import { MessageMarkdown } from "@/components/features/agent/MessageMarkdown";
import { IngestionPlanCard } from "@/components/features/agent/IngestionPlanCard";
import type { BatchPlan, DefaultEventPlan } from "@/lib/ingestion";
import { Check, FileText, Loader2, Send, Upload, Wrench, X } from "lucide-react";

// ── 型定義 ──────────────────────────────────────────────────────────────────

interface ToolCallEvent {
  tool_name: string;
  args: Record<string, unknown>;
}

interface DeliverableData {
  deliverable_id?: string;
  email_id?: string;
  person_id?: string;
  contact_id?: string;
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
  contact_name?: string;
  contact_company?: string;
  engagement_level?: string;
}

interface CodeBlock {
  code: string;
  output?: string;
  outcome?: string;
}

// チャット timeline のメッセージ型
type ChatMessage =
  | {
      id: string;
      role: "user";
      content: string;
      files?: { name: string }[];
    }
  | {
      id: string;
      role: "assistant";
      content: string;
      toolCalls?: ToolCallEvent[];
      codeBlocks?: CodeBlock[];
      deliverables?: DeliverableData[];
      runId?: string;
      loading?: boolean;
      pendingConfirm?: {
        files: File[];
        hint: string;
        plan: BatchPlan | null; // null = プラン生成失敗（実行側で Understand を1回だけ実行）
        proposedEvent: DefaultEventPlan | null; // AI 提案の原本（「イベントなし」解除時の復元用）
      };
      // 取り込みメッセージ専用。一度セットした ingestionPlan は完了後も消さない
      // （結果表示に上書きされてプランが消える問題への対策）。
      ingestionPlan?: BatchPlan | null;
      ingestionProgress?: { stage: string } | null;
      ingestionResult?: {
        report_markdown: string;
        created_entities: Record<string, number>;
        pending_count: number;
        skipped_count: number;
      } | null;
    };

const INITIAL_MESSAGE: ChatMessage = {
  id: "init",
  role: "assistant",
  content:
    "こんにちは。AIエージェントです。\n\nファイルを添付してデータを取り込むか、チャットで指示をお送りください。\n\n例: 「2025秋の展示会の振り返りをして」「顧客ごとのフォローアップ案を作って」「プロダクトAへの関心が高いリストを分析して」",
};

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
              {b.output !== undefined && (
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

// ── 取り込み進捗ステージのラベル ────────────────────────────────────────────────

const STAGE_LABEL: Record<string, string> = {
  read: "データを読み込み中",
  interpret: "内容を解釈中",
  conform: "重複や表記ゆれを確認中",
  bind: "データをひも付け中",
  derive: "分析用の情報を生成中",
  report: "レポートを作成中",
};

function stageLabel(stage: string): string {
  return STAGE_LABEL[stage] ?? "取り込みを準備中";
}

// ── メインコンポーネント ──────────────────────────────────────────────────────

export default function DashboardPage() {
  const { activeSpace } = useSpace();
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const sessionId = useRef<string>("");
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const { threads, reload: reloadThreads, rename: renameThread, remove: removeThread } = useThreads();
  const [messages, setMessages] = useState<ChatMessage[]>([INITIAL_MESSAGE]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const pollingRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const [isIngesting, setIsIngesting] = useState(false);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    return () => {
      Object.values(pollingRefs.current).forEach(clearInterval);
    };
  }, []);

  // ── 取り込みフロー（ファイル添付あり時のルート）─────────────────────────────

  async function handleIngestionFlow(files: File[], hint: string) {
    const userMsgId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      {
        id: userMsgId,
        role: "user",
        content: hint || `${files.length === 1 ? `「${files[0].name}」` : `${files.length}件のファイル`}を添付しました`,
        files: files.map((f) => ({ name: f.name })),
      },
    ]);

    const asstMsgId = crypto.randomUUID();
    setMessages((prev) => [...prev, { id: asstMsgId, role: "assistant", content: "", loading: true }]);
    setIsIngesting(true);

    try {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      if (hint.trim()) formData.append("hint", hint.trim());

      const res = await authFetch("/api/integration/plan", { method: "POST", body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー ${res.status}`);
      }
      const plan: BatchPlan = await res.json();

      setMessages((prev) =>
        prev.map((m) =>
          m.id === asstMsgId
            ? {
                ...m,
                content: "この内容（と下の既定イベント）で取り込みますか？",
                loading: false,
                pendingConfirm: { files, hint, plan, proposedEvent: plan.default_event },
                ingestionPlan: plan,
              }
            : m
        )
      );
    } catch (e) {
      // プラン生成失敗: 実行側で Understand を1回だけ実行する経路（plan なし）へ委ねる
      setMessages((prev) =>
        prev.map((m) =>
          m.id === asstMsgId
            ? {
                ...m,
                content: `プランの提示に失敗しました（${(e as Error).message}）。このまま取り込みを実行しますか？`,
                loading: false,
                pendingConfirm: { files, hint, plan: null, proposedEvent: null },
              }
            : m
        )
      );
    } finally {
      setIsIngesting(false);
    }
  }

  /** 確認ブロックの既定イベント編集（名前変更 / 「イベントなし」トグル）。 */
  function updateDefaultEvent(msgId: string, next: DefaultEventPlan | null) {
    setMessages((prev) =>
      prev.map((m) => {
        if (m.id !== msgId || m.role !== "assistant" || !m.pendingConfirm?.plan) return m;
        const plan = { ...m.pendingConfirm.plan, default_event: next };
        return {
          ...m,
          pendingConfirm: { ...m.pendingConfirm, plan },
          ingestionPlan: plan,
        };
      })
    );
  }

  async function handleConfirmIngestion(
    msgId: string,
    pending: { files: File[]; hint: string; plan: BatchPlan | null }
  ) {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === msgId
          ? { ...m, content: "", pendingConfirm: undefined, loading: true, ingestionProgress: { stage: "" } }
          : m
      )
    );
    setIsIngesting(true);

    try {
      const formData = new FormData();
      pending.files.forEach((f) => formData.append("files", f));
      if (pending.hint.trim()) formData.append("hint", pending.hint.trim());
      // 承認済みプラン（既定イベントの修正を含む）をそのまま実行に渡す（ADR-015 決定4）
      if (pending.plan) formData.append("plan", JSON.stringify(pending.plan));

      const res = await authFetch("/api/integration/batches", { method: "POST", body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー ${res.status}`);
      }
      const { batch_id } = await res.json();
      // バックエンドがこの時点で取り込みスレッドを作成済み。左メニューに即時反映する。
      reloadThreads();
      pollBatch(batch_id, msgId);
    } catch (e) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === msgId
            ? { ...m, content: `取り込みエラー: ${(e as Error).message}`, loading: false, ingestionProgress: null }
            : m
        )
      );
      setIsIngesting(false);
    }
  }

  function handleCancelIngestion(msgId: string) {
    setMessages((prev) =>
      prev.map((m) => (m.id === msgId ? { ...m, pendingConfirm: undefined } : m))
    );
  }

  function pollBatch(batchId: string, msgId: string) {
    const timer = setInterval(async () => {
      try {
        const res = await authFetch(`/api/integration/batches/${batchId}`);
        if (!res.ok) return;
        const data = await res.json();

        if (data.status === "done") {
          clearInterval(timer);
          setIsIngesting(false);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === msgId
                ? {
                    ...m,
                    loading: false,
                    ingestionProgress: null,
                    ingestionResult: {
                      report_markdown: (data.report_markdown as string) || "取り込みが完了しました。",
                      created_entities: data.created_entities ?? {},
                      pending_count: (data.pending_count as number) ?? 0,
                      skipped_count: (data.skipped_count as number) ?? 0,
                    },
                  }
                : m
            )
          );
        } else if (data.status === "error") {
          clearInterval(timer);
          setIsIngesting(false);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === msgId
                ? {
                    ...m,
                    content: `取り込みエラー: ${data.error ?? "不明なエラー"}`,
                    loading: false,
                    ingestionProgress: null,
                  }
                : m
            )
          );
        } else {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === msgId ? { ...m, ingestionProgress: { stage: data.stage ?? "" } } : m
            )
          );
        }
      } catch {
        // keep polling
      }
    }, 2000);
  }

  // ── SSE チャット ──────────────────────────────────────────────────────────

  async function handleChatFlow(text: string) {
    setSending(true);

    const userMsgId = crypto.randomUUID();
    setMessages((prev) => [...prev, { id: userMsgId, role: "user", content: text }]);

    const asstMsgId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: asstMsgId, role: "assistant", content: "", toolCalls: [], loading: true },
    ]);

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
            toolCalls = [
              ...toolCalls,
              { tool_name: event.tool_name as string, args: (event.args ?? {}) as Record<string, unknown> },
            ];
            setMessages((prev) =>
              prev.map((m) => (m.id === asstMsgId ? { ...m, toolCalls, loading: true } : m))
            );
          } else if (event.type === "tool_result") {
            const result = (event.result ?? {}) as Record<string, unknown>;
            const inner = (result.result ?? result) as Record<string, unknown>;
            let parsed: Record<string, unknown> = inner;
            if (typeof inner === "string") {
              try {
                parsed = JSON.parse(inner);
              } catch {
                parsed = {};
              }
            }
            if (event.tool_name === "run_assembly" && typeof parsed.run_id === "string") {
              toolRunId = parsed.run_id as string;
            }
          } else if (event.type === "code") {
            codeBlocks = [...codeBlocks, { code: event.code as string }];
            setMessages((prev) =>
              prev.map((m) => (m.id === asstMsgId ? { ...m, codeBlocks, toolCalls, loading: true } : m))
            );
          } else if (event.type === "code_result") {
            const lastIdx = codeBlocks.map((b) => b.output === undefined).lastIndexOf(true);
            if (lastIdx >= 0) {
              codeBlocks = codeBlocks.map((b, i) =>
                i === lastIdx
                  ? { ...b, output: (event.output as string) ?? "", outcome: event.outcome as string }
                  : b
              );
              setMessages((prev) =>
                prev.map((m) => (m.id === asstMsgId ? { ...m, codeBlocks, loading: true } : m))
              );
            }
          } else if (event.type === "text") {
            accText += event.text as string;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstMsgId ? { ...m, content: accText, toolCalls, codeBlocks, loading: true } : m
              )
            );
          } else if (event.type === "done") {
            const detectedRunId = toolRunId ?? extractRunId(accText);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstMsgId
                  ? { ...m, content: accText, toolCalls, loading: false, runId: detectedRunId ?? undefined }
                  : m
              )
            );
            if (detectedRunId) startRunPolling(asstMsgId, detectedRunId);
          } else if (event.type === "error") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstMsgId
                  ? { ...m, content: `エラーが発生しました: ${event.message as string}`, loading: false }
                  : m
              )
            );
          }
        }
      }

      setMessages((prev) =>
        prev.map((m) => (m.id === asstMsgId && m.role === "assistant" && m.loading ? { ...m, loading: false } : m))
      );
    } catch (e) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === asstMsgId ? { ...m, content: `エラー: ${(e as Error).message}`, loading: false } : m
        )
      );
    } finally {
      setSending(false);
      reloadThreads();
    }
  }

  async function handleSend() {
    if (pendingFiles.length > 0) {
      const files = [...pendingFiles];
      const hint = input.trim();
      setPendingFiles([]);
      setInput("");
      await handleIngestionFlow(files, hint);
      return;
    }

    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    await handleChatFlow(text);
  }

  // ── スレッド（会話）切替 ─────────────────────────────────────────────────

  function handleNewChat() {
    if (sending) return;
    sessionId.current = "";
    setActiveThreadId(null);
    setMessages([INITIAL_MESSAGE]);
  }

  async function handleSelectThread(threadId: string) {
    if (sending || threadId === activeThreadId) return;
    const stored = await getThreadMessages(threadId);
    const thread = threads.find((t) => t.thread_id === threadId);
    sessionId.current = threadId;
    setActiveThreadId(threadId);

    if (thread?.kind === "ingestion") {
      // 取り込みスレッドは会話メッセージではなく plan/result のスナップショットなので、
      // 通常のチャットUIとは別経路で単一の合成メッセージに組み立てる。
      const planMsg = stored.find((m) => m.content_type === "ingestion_plan");
      const resultMsg = stored.find((m) => m.content_type === "ingestion_result");
      const errorMsg = stored.find((m) => m.content_type === "ingestion_error");
      const msgId = crypto.randomUUID();
      const stillRunning = !resultMsg && !errorMsg;
      setMessages([
        {
          id: msgId,
          role: "assistant",
          content: errorMsg ? `取り込みエラー: ${errorMsg.error ?? "不明なエラー"}` : "",
          ingestionPlan: planMsg?.plan ?? null,
          ingestionResult: resultMsg
            ? {
                report_markdown: resultMsg.report_markdown ?? "",
                created_entities: resultMsg.created_entities ?? {},
                pending_count: resultMsg.pending_count ?? 0,
                skipped_count: resultMsg.skipped_count ?? 0,
              }
            : null,
          ingestionProgress: stillRunning ? { stage: "" } : null,
          loading: stillRunning,
        },
      ]);
      if (stillRunning) {
        // 実行中に離脱して戻ってきたケース: batch_id === thread_id なのでそのまま再開できる
        setIsIngesting(true);
        pollBatch(threadId, msgId);
      }
      return;
    }

    const restored: ChatMessage[] = stored.map((m) => ({
      id: crypto.randomUUID(),
      role: m.role,
      content: m.content,
      toolCalls: m.tool_calls,
      codeBlocks: m.code_blocks,
      runId: m.run_id ?? undefined,
      loading: false,
    }));
    setMessages(restored.length > 0 ? restored : [INITIAL_MESSAGE]);
    restored.forEach((m) => {
      if (m.role === "assistant" && m.runId) loadRunDeliverables(m.id, m.runId);
    });
  }

  // ── 成果物ポーリング ─────────────────────────────────────────────────────

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
      setMessages((prev) => prev.map((m) => (m.id === msgId ? { ...m, deliverables } : m)));
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

  // ── activeSpace 切替でリセット ────────────────────────────────────────────

  const prevSpaceId = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (activeSpace && prevSpaceId.current && prevSpaceId.current !== activeSpace.space_id) {
      sessionId.current = "";
      setActiveThreadId(null);
      setMessages([INITIAL_MESSAGE]);
      setPendingFiles([]);
    }
    prevSpaceId.current = activeSpace?.space_id;
  }, [activeSpace]);

  // ── レンダリング ─────────────────────────────────────────────────────────

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

        {/* メッセージ履歴 */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
          {messages.map((msg) => {
            // user メッセージ
            if (msg.role === "user") {
              return (
                <div key={msg.id} className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed bg-brand-600 text-white rounded-br-sm">
                    {msg.files && msg.files.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mb-2">
                        {msg.files.map((f, i) => (
                          <span
                            key={i}
                            className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-white/20 text-white"
                          >
                            <FileText className="w-3 h-3" />
                            {f.name}
                          </span>
                        ))}
                      </div>
                    )}
                    {msg.content && <MessageMarkdown content={msg.content} />}
                  </div>
                </div>
              );
            }

            // assistant メッセージ
            return (
              <div key={msg.id} className="flex justify-start">
                <div className="max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed bg-gray-50 border border-gray-200 text-gray-700 rounded-bl-sm">
                  {msg.toolCalls && msg.toolCalls.length > 0 && (
                    <ToolCallIndicator toolCalls={msg.toolCalls} />
                  )}
                  {msg.codeBlocks && msg.codeBlocks.length > 0 && (
                    <CodeExecutionPanel blocks={msg.codeBlocks} />
                  )}
                  {msg.loading && !msg.content && !msg.ingestionProgress && (
                    <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                  )}
                  {msg.ingestionPlan && (
                    <div className="mt-1">
                      <IngestionPlanCard plan={msg.ingestionPlan} />
                    </div>
                  )}
                  {msg.content && (
                    <div className="mt-1">
                      <MessageMarkdown content={msg.content} />
                    </div>
                  )}
                  {msg.loading && msg.content && (
                    <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-gray-400 animate-pulse rounded-sm align-middle" />
                  )}
                  {msg.pendingConfirm && (
                    <div className="mt-3 pt-2 border-t border-gray-100 space-y-2">
                      {msg.pendingConfirm.plan && (
                        <div className="space-y-1">
                          <div className="flex items-center gap-2 text-xs">
                            <span className="text-gray-500 shrink-0">既定イベント:</span>
                            <input
                              type="text"
                              value={msg.pendingConfirm.plan.default_event?.name ?? ""}
                              disabled={msg.pendingConfirm.plan.default_event === null}
                              placeholder="（なし）"
                              onChange={(e) => {
                                const cur = msg.pendingConfirm!.plan!.default_event;
                                updateDefaultEvent(msg.id, {
                                  name: e.target.value,
                                  is_existing:
                                    cur?.name === e.target.value ? (cur?.is_existing ?? false) : false,
                                  evidence: cur?.evidence ?? "",
                                });
                              }}
                              className="flex-1 min-w-0 rounded-lg border border-gray-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400 disabled:bg-gray-100 disabled:text-gray-400"
                            />
                            {msg.pendingConfirm.plan.default_event && (
                              <span
                                className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded-full ${
                                  msg.pendingConfirm.plan.default_event.is_existing
                                    ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                                    : "bg-amber-50 text-amber-700 border border-amber-200"
                                }`}
                              >
                                {msg.pendingConfirm.plan.default_event.is_existing ? "既存" : "新規"}
                              </span>
                            )}
                            <label className="shrink-0 flex items-center gap-1 text-[11px] text-gray-500">
                              <input
                                type="checkbox"
                                checked={msg.pendingConfirm.plan.default_event === null}
                                onChange={(e) =>
                                  updateDefaultEvent(
                                    msg.id,
                                    e.target.checked ? null : (msg.pendingConfirm!.proposedEvent ?? { name: "", is_existing: false, evidence: "" })
                                  )
                                }
                              />
                              イベントなし
                            </label>
                          </div>
                          {msg.pendingConfirm.plan.default_event?.evidence && (
                            <div className="text-[11px] text-gray-400">
                              提案根拠: {msg.pendingConfirm.plan.default_event.evidence}
                            </div>
                          )}
                          <div className="text-[11px] text-gray-400">
                            行にイベント列がある場合はそちらが優先されます。どちらも無い行は保留になります。
                          </div>
                        </div>
                      )}
                      <div className="flex gap-2">
                        <button
                          onClick={() => handleConfirmIngestion(msg.id, msg.pendingConfirm!)}
                          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition"
                        >
                          <Check className="w-3.5 h-3.5" /> 取り込む
                        </button>
                        <button
                          onClick={() => handleCancelIngestion(msg.id)}
                          className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 text-gray-600 hover:bg-white transition"
                        >
                          キャンセル
                        </button>
                      </div>
                    </div>
                  )}
                  {msg.ingestionProgress && (
                    <div className="mt-3 flex items-center gap-2 text-xs text-gray-500">
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-indigo-500" />
                      {stageLabel(msg.ingestionProgress.stage)}...
                    </div>
                  )}
                  {msg.ingestionResult && (
                    <div className="mt-3 pt-2 border-t border-gray-100 space-y-2">
                      <MessageMarkdown content={msg.ingestionResult.report_markdown} />
                      {msg.ingestionResult.pending_count > 0 && (
                        <p className="text-[11px] text-amber-700">
                          ⚠️ 保留 {msg.ingestionResult.pending_count} 件（イベント未確定の行）は「データ」タブの「取り込み行（着地）」から確認できます。
                        </p>
                      )}
                      <p className="text-[11px] text-gray-400">データの確認は「データ」タブから行えます。</p>
                    </div>
                  )}
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
            );
          })}
          <div ref={messagesEndRef} />
        </div>

        {/* 入力エリア */}
        <div className="shrink-0 border-t border-gray-100 px-6 py-3">
          {/* 添付ファイルチップ */}
          {pendingFiles.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {pendingFiles.map((f, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-indigo-50 border border-indigo-200 text-indigo-700"
                >
                  <FileText className="w-3 h-3" />
                  {f.name}
                  <button
                    onClick={() => setPendingFiles((prev) => prev.filter((_, j) => j !== i))}
                    className="ml-0.5 text-indigo-400 hover:text-indigo-600"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))}
            </div>
          )}

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".csv,.xlsx,.xls,.txt,.docx,.pdf,.pptx"
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length) setPendingFiles((prev) => [...prev, ...files]);
              e.target.value = "";
            }}
          />
          <form onSubmit={(e) => { e.preventDefault(); handleSend(); }} className="flex gap-2">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isIngesting}
              className="flex items-center justify-center w-10 h-10 rounded-xl border border-gray-200 text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition disabled:opacity-40 shrink-0"
              title="ファイルを添付"
            >
              <Upload className="w-4 h-4" />
            </button>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                pendingFiles.length > 0
                  ? "ヒントを入力（任意）"
                  : "指示を入力してください（例: 顧客ごとのフォローアップ案を作って）"
              }
              disabled={sending}
              className="flex-1 rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 disabled:bg-gray-50 disabled:text-gray-400"
            />
            <button
              type="submit"
              disabled={(!input.trim() && pendingFiles.length === 0) || sending}
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
