"use client";

import { useEffect, useRef, useState } from "react";
import { auth } from "@/lib/firebase";
import { FileDropzone } from "@/components/features/upload/FileDropzone";
import { EmailBlockCard } from "@/components/features/email/EmailBlockCard";
import { Download, RefreshCw, Send, Loader2 } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function authFetch(path: string, init?: RequestInit): Promise<Response> {
  const token = await auth.currentUser?.getIdToken();
  return fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
}

// ---- 型定義 ----

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  emails?: EmailData[];
  loading?: boolean;
}

interface EmailData {
  lead_id: string;
  subject: string;
  blocks: {
    block_type: string;
    reason_for_inclusion: string;
    associated_content_ids: string[];
    block_text: string;
  }[];
  lead_name?: string;
  lead_company?: string;
  lead_segment?: string;
}

interface BatchStatus {
  status: string;
  filename: string;
  total: number;
  lead_count: number;
  segment_counts: Record<string, number>;
  error?: string;
}

interface ExecuteStatus {
  execution_status: string;
  execution_done: number;
  lead_count: number;
  email_count: number;
  execution_error?: string;
}

// ---- コマンド判定 ----

function isGenerateCommand(text: string): boolean {
  return /メール|生成|generate|作成|おねがい|お願い/.test(text);
}

function isLeadsCommand(text: string): boolean {
  return /リード|leads|一覧|確認|見せ/.test(text);
}

function isDownloadCommand(text: string): boolean {
  return /ダウンロード|download|CSV|csv|エクスポート/.test(text);
}

// ---- メインコンポーネント ----

export default function DashboardPage() {
  const [file, setFile] = useState<File | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null);
  const [ingesting, setIngesting] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: "assistant",
      content:
        "展示会リストをアップロードしてください。CSVまたはExcelファイルに対応しています。取り込み後、「メールを生成して」とメッセージを送ると個別最適化メールを生成します。",
    },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // ---- ファイルアップロード → Ingestion Agent ----

  async function handleFileSelected(f: File | null) {
    setFile(f);
    if (!f) return;

    setIngesting(true);
    addAssistantMessage(`「${f.name}」を受け取りました。リードデータを取り込んでいます...`, true);

    try {
      const formData = new FormData();
      formData.append("file", f);
      const res = await authFetch("/api/ingest", { method: "POST", body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー: ${res.status}`);
      }
      const { batch_id } = await res.json();
      setBatchId(batch_id);
      pollIngestion(batch_id);
    } catch (e) {
      replaceLastAssistantMessage(`取り込みに失敗しました: ${(e as Error).message}`);
      setIngesting(false);
    }
  }

  function pollIngestion(id: string) {
    const timer = setInterval(async () => {
      try {
        const res = await authFetch(`/api/batches/${id}`);
        if (!res.ok) return;
        const data: BatchStatus = await res.json();

        if (data.status === "done") {
          clearInterval(timer);
          setBatchStatus(data);
          setIngesting(false);

          const counts = data.segment_counts ?? {};
          const parts = [
            counts["アポ獲得済み"] ? `アポ獲得済み ${counts["アポ獲得済み"]}件` : null,
            counts["アポなし・感度高"] ? `感度高 ${counts["アポなし・感度高"]}件` : null,
            counts["通常リード"] ? `通常リード ${counts["通常リード"]}件` : null,
          ].filter(Boolean);

          replaceLastAssistantMessage(
            `**${data.lead_count}件**のリードを取り込みました。\n` +
              (parts.length ? `セグメント内訳: ${parts.join(" / ")}\n\n` : "") +
              "「メールを生成して」と送るとパーソナライズメールを作成します。"
          );
        } else if (data.status === "error") {
          clearInterval(timer);
          setIngesting(false);
          replaceLastAssistantMessage(`取り込みエラー: ${data.error ?? "不明なエラー"}`);
        }
      } catch {
        // network error — keep polling
      }
    }, 2000);
  }

  // ---- チャット送信 ----

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setSending(true);

    setMessages((prev) => [...prev, { role: "user", content: text }]);

    try {
      if (isGenerateCommand(text)) {
        await handleExecute();
      } else if (isLeadsCommand(text)) {
        await handleShowLeads();
      } else if (isDownloadCommand(text)) {
        await handleDownload();
        addAssistantMessage("CSVのダウンロードを開始しました。");
      } else {
        addAssistantMessage(
          "以下のコマンドが使えます:\n" +
            "• **メールを生成して** — 取り込み済みリードの個別メールを生成\n" +
            "• **リードを確認** — 取り込み済みリードの一覧\n" +
            "• **ダウンロード** — 生成済みメールをCSVで出力"
        );
      }
    } finally {
      setSending(false);
    }
  }

  // ---- Execution Agent ----

  async function handleExecute() {
    if (!batchId) {
      addAssistantMessage("先にファイルをアップロードしてリードを取り込んでください。");
      return;
    }

    addAssistantMessage(`${batchStatus?.lead_count ?? ""}件のメールを生成しています...`, true);

    try {
      const res = await authFetch("/api/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ batch_id: batchId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `エラー: ${res.status}`);
      }
      pollExecution(batchId);
    } catch (e) {
      replaceLastAssistantMessage(`メール生成の開始に失敗しました: ${(e as Error).message}`);
    }
  }

  function pollExecution(id: string) {
    const timer = setInterval(async () => {
      try {
        const res = await authFetch(`/api/execute/${id}/status`);
        if (!res.ok) return;
        const data: ExecuteStatus = await res.json();

        if (data.execution_done > 0 && data.lead_count > 0) {
          const pct = Math.round((data.execution_done / data.lead_count) * 100);
          updateLoadingMessage(`メールを生成しています... ${data.execution_done}/${data.lead_count} 件 (${pct}%)`);
        }

        if (data.execution_status === "done") {
          clearInterval(timer);
          await loadEmails(id, data.email_count);
        } else if (data.execution_status === "error") {
          clearInterval(timer);
          replaceLastAssistantMessage(
            `メール生成エラー: ${data.execution_error ?? "不明なエラー"}`
          );
        }
      } catch {
        // keep polling
      }
    }, 2000);
  }

  async function loadEmails(id: string, count: number) {
    try {
      const [emailsRes, leadsRes] = await Promise.all([
        authFetch(`/api/execute/${id}/emails`),
        authFetch(`/api/batches/${id}/leads`),
      ]);
      const { emails } = await emailsRes.json();
      const { leads } = await leadsRes.json();

      const leadsById: Record<string, { name: string; company_name: string; segment: string }> =
        {};
      for (const lead of leads) {
        leadsById[lead.lead_id] = lead;
      }

      const emailData: EmailData[] = emails.map(
        (e: { lead_id: string; subject: string; blocks: EmailData["blocks"] }) => ({
          ...e,
          lead_name: leadsById[e.lead_id]?.name,
          lead_company: leadsById[e.lead_id]?.company_name,
          lead_segment: leadsById[e.lead_id]?.segment,
        })
      );

      setMessages((prev) => {
        const next = [...prev];
        const idx = next.findLastIndex((m) => m.role === "assistant" && m.loading);
        if (idx >= 0) {
          next[idx] = {
            role: "assistant",
            content: `**${count}件**のメールを生成しました。「ダウンロード」でCSVを取得できます。`,
            emails: emailData,
          };
        }
        return next;
      });
    } catch (e) {
      replaceLastAssistantMessage(`メール取得に失敗しました: ${(e as Error).message}`);
    }
  }

  // ---- リード一覧 ----

  async function handleShowLeads() {
    if (!batchId) {
      addAssistantMessage("先にファイルをアップロードしてリードを取り込んでください。");
      return;
    }
    try {
      const res = await authFetch(`/api/batches/${batchId}/leads`);
      const { leads } = await res.json();
      const lines = leads
        .slice(0, 10)
        .map(
          (l: { name: string; company_name: string; segment: string; extracted_challenge: string }, i: number) =>
            `${i + 1}. **${l.name}**（${l.company_name}）— ${l.segment}\n   課題: ${l.extracted_challenge}`
        )
        .join("\n");
      addAssistantMessage(
        `取り込み済みリード（先頭10件）:\n\n${lines}` +
          (leads.length > 10 ? `\n\n他 ${leads.length - 10}件` : "")
      );
    } catch (e) {
      addAssistantMessage(`リード取得に失敗しました: ${(e as Error).message}`);
    }
  }

  // ---- CSVダウンロード ----

  async function handleDownload() {
    if (!batchId) return;
    const token = await auth.currentUser?.getIdToken();
    const res = await fetch(`${API_BASE}/api/execute/${batchId}/download`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      addAssistantMessage("ダウンロードに失敗しました。先にメールを生成してください。");
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `emails_${batchId.slice(0, 8)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ---- メッセージ操作ヘルパー ----

  function addAssistantMessage(content: string, loading = false) {
    setMessages((prev) => [...prev, { role: "assistant", content, loading }]);
  }

  function replaceLastAssistantMessage(content: string) {
    setMessages((prev) => {
      const next = [...prev];
      const idx = next.findLastIndex((m) => m.role === "assistant");
      if (idx >= 0) next[idx] = { role: "assistant", content };
      return next;
    });
  }

  function updateLoadingMessage(content: string) {
    setMessages((prev) => {
      const next = [...prev];
      const idx = next.findLastIndex((m) => m.role === "assistant" && m.loading);
      if (idx >= 0) next[idx] = { role: "assistant", content, loading: true };
      return next;
    });
  }

  function reset() {
    setFile(null);
    setBatchId(null);
    setBatchStatus(null);
    setIngesting(false);
    setMessages([
      {
        role: "assistant",
        content:
          "リセットしました。新しいファイルをアップロードしてください。",
      },
    ]);
  }

  // ---- レンダリング ----

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)] max-w-3xl mx-auto">
      <div className="flex items-center justify-between py-4 shrink-0">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Agenticメールジェネレーター</h1>
          <p className="text-xs text-gray-400 mt-0.5">
            リストをアップロードして「メールを生成して」と送るだけ
          </p>
        </div>
        <button
          onClick={reset}
          className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-700 border border-gray-200 rounded-lg px-3 py-1.5 transition"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          リセット
        </button>
      </div>

      {/* ファイルアップロードエリア */}
      {!batchId && (
        <div className="shrink-0 mb-4">
          <FileDropzone
            onFileSelected={handleFileSelected}
            selectedFile={file}
            disabled={ingesting}
          />
        </div>
      )}

      {/* バッチ情報バー */}
      {batchStatus && (
        <div className="shrink-0 mb-3 flex items-center justify-between bg-brand-50 border border-brand-100 rounded-xl px-4 py-2.5 text-sm">
          <span className="text-brand-700 font-medium">
            {batchStatus.filename} — {batchStatus.lead_count}件取込済み
          </span>
          <div className="flex gap-2">
            <button
              onClick={handleDownload}
              className="flex items-center gap-1 text-xs text-brand-600 hover:text-brand-800 transition"
            >
              <Download className="w-3.5 h-3.5" />
              CSV
            </button>
            <button
              onClick={reset}
              className="text-xs text-gray-400 hover:text-gray-600 transition"
            >
              変更
            </button>
          </div>
        </div>
      )}

      {/* チャットメッセージ */}
      <div className="flex-1 overflow-y-auto space-y-4 pb-4 pr-1">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                msg.role === "user"
                  ? "bg-brand-600 text-white rounded-br-sm"
                  : "bg-white border border-gray-200 text-gray-700 rounded-bl-sm shadow-sm"
              }`}
            >
              {msg.loading && (
                <Loader2 className="w-3.5 h-3.5 animate-spin inline mr-2 text-gray-400" />
              )}
              {/* Markdown風テキスト（簡易） */}
              <span
                dangerouslySetInnerHTML={{
                  __html: msg.content
                    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
                    .replace(/\n/g, "<br/>"),
                }}
              />

              {/* メール結果 */}
              {msg.emails && msg.emails.length > 0 && (
                <div className="mt-3 space-y-2">
                  {msg.emails.map((email, j) => (
                    <EmailBlockCard key={j} email={email} index={j} />
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* チャット入力 */}
      <div className="shrink-0 pt-3 border-t border-gray-100">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSend();
          }}
          className="flex gap-2"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              batchId
                ? "「メールを生成して」「リードを確認」「ダウンロード」"
                : "ファイルをアップロードしてください"
            }
            disabled={sending || ingesting || !batchId}
            className="flex-1 rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400 disabled:bg-gray-50 disabled:text-gray-400"
          />
          <button
            type="submit"
            disabled={!input.trim() || sending || ingesting || !batchId}
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
  );
}
