"use client";

import { useCallback, useEffect, useState } from "react";
import { authFetch } from "@/lib/api";
import { useSpace } from "@/lib/space-context";
import {
  Calendar,
  DollarSign,
  Award,
  Smile,
  FileText,
  CheckCircle2,
  Info,
  X,
  FileSpreadsheet,
  User,
  Activity,
  Layers,
  HelpCircle,
  Database,
} from "lucide-react";

const formatCurrency = (amount: number) =>
  new Intl.NumberFormat("ja-JP", { style: "currency", currency: "JPY" }).format(amount);

const getStatusBadgeClass = (status: string) => {
  switch (status) {
    case "終了":
      return "bg-gray-100 text-gray-500 border border-gray-200";
    case "開催中":
      return "bg-emerald-50 text-emerald-600 border border-emerald-200";
    default:
      return "bg-blue-50 text-blue-500 border border-blue-200";
  }
};

// ── 型定義 ──────────────────────────────────────────────────────────────────

interface EventDetail {
  event_id: string;
  name: string;
  event_type: string;
  status: string;
  venue: string;
  event_date: string;
  event_date_end: string;
  booth_number?: string | null;
  total_budget: number;
  target_contact_count: number;
  description: string;
  created_at: string;
}

interface EventKPI {
  kpi_id: string;
  event_id: string;
  total_visitors_to_booth: number;
  total_contacts_collected: number;
  appointments_booked: number;
  demo_sessions_held: number;
  follow_email_open_rate: number;
  follow_email_reply_rate: number;
  pipeline_value_jpy: number;
  closed_deals_3m: number;
  closed_revenue_3m_jpy: number;
  contacts_by_engagement?: {
    appointment_booked: number;
    high_intent: number;
    nurturing: number;
  };
}

interface CostItem {
  cost_id: string;
  event_id: string;
  category: string;
  description: string;
  amount_jpy: number;
  vendor_name?: string | null;
  invoice_date?: string | null;
}

interface CostSummary {
  total_jpy: number;
  by_category: Record<string, number>;
}

interface SatisfactionScore {
  category: string;
  avg_score: number;
  response_count: number;
}

interface SurveyResponse {
  survey_id: string;
  event_id: string;
  total_responses: number;
  nps_score: number;
  nps_promoters: number;
  nps_passives: number;
  nps_detractors: number;
  satisfaction_scores: SatisfactionScore[];
  verbatim_positives: string[];
  verbatim_negatives: string[];
  verbatim_suggestions: string[];
}

interface BatchSummary {
  batch_id: string;
  status: string;
  filenames: string[];
  filename: string;
  files: { filename: string; status: string }[];
  event_id?: string;
  created_at: string;
  created_entities?: Record<string, number>;
  partial?: boolean;
}

interface Contact {
  contact_id: string;
  name: string;
  company_name: string;
  department: string;
  job_title: string;
  email?: string | null;
  stage: string;
  engagement_level?: string | null;
  interested_products: string[];
  extracted_challenge: string;
  notes: string;
}

// ── Lineage 型定義 ────────────────────────────────────────────────────────────

interface TransformDecision {
  field: string;
  value: string;
  reason: string;
  source_signals?: Record<string, string>;
}

interface EntityTransformation {
  entity_type: string;
  entity_id: string;
  source_label: string;
  decisions: TransformDecision[];
}

interface SkippedRecord {
  entity_type: string;
  reason: string;
  detail?: string;
}

interface LineageReport {
  source: {
    filename: string;
    source_type: "tabular" | "unstructured";
    batch_id: string;
    created_at: string;
  };
  stage1_ai: {
    column_mapping?: {
      entity_type: string;
      column_map: Record<string, string>;
      unmapped_columns: string[];
    } | null;
    raw_extraction?: Record<string, any> | null;
  };
  stage2_transformations: {
    transformations: EntityTransformation[];
    skipped_records: SkippedRecord[];
  };
  summary?: {
    entity_counts: Record<string, number>;
    engagement_breakdown: Record<string, number>;
    product_breakdown: Record<string, number>;
    skipped_count: number;
  } | null;
  created_entity_ids?: Record<string, string[]>;
}

// ── 全イベント横断サマリ (イベント未選択時) ──────────────────────────────────

interface EventSummaryRow {
  event_id: string;
  name: string;
  event_date: string;
  status: string;
  event_type: string;
  total_visitors_to_booth: number;
  total_contacts_collected: number;
  cost_total_jpy: number;
}

interface SummaryTotals {
  event_count: number;
  total_cost_jpy: number;
  total_visitors: number;
  total_contacts: number;
}

function AllEventsSummary() {
  const { activeSpace } = useSpace();
  const [rows, setRows] = useState<EventSummaryRow[]>([]);
  const [totals, setTotals] = useState<SummaryTotals | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // アクティブスペース未確定のうちは X-Space-Id を付与できず 422 になるため叩かない
    if (!activeSpace) {
      setLoading(false);
      return;
    }
    let active = true;
    (async () => {
      setLoading(true);
      try {
        const res = await authFetch("/api/events/summary");
        if (res.ok && active) {
          const data = await res.json();
          setRows(data.events ?? []);
          setTotals(data.totals ?? null);
        }
      } catch (e) {
        console.error("Failed to fetch events summary", e);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [activeSpace]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="px-6 py-4 border-b border-gray-200 bg-gray-50/60 shrink-0">
        <div className="flex items-center gap-2">
          <Database className="w-4 h-4 text-gray-400" />
          <h1 className="text-lg font-extrabold text-gray-900 leading-tight">全イベント横断サマリ</h1>
        </div>
        <p className="text-xs text-gray-400 mt-1">
          左のリストからイベントを選択すると、そのイベントの詳細データとチャット文脈に切り替わります。
        </p>
      </div>

      <div className="px-6 py-6 space-y-6">
        {/* 合計カード */}
        {totals && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex items-center gap-3">
              <div className="p-3 bg-gray-50 text-gray-600 rounded-xl">
                <Calendar className="w-5 h-5" />
              </div>
              <div>
                <span className="text-[11px] font-bold text-gray-400 block uppercase">イベント数</span>
                <span className="text-xl font-black font-mono">{totals.event_count}</span>
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex items-center gap-3">
              <div className="p-3 bg-blue-50 text-blue-600 rounded-xl">
                <Layers className="w-5 h-5" />
              </div>
              <div>
                <span className="text-[11px] font-bold text-gray-400 block uppercase">総来場数</span>
                <span className="text-xl font-black font-mono">{totals.total_visitors.toLocaleString()}</span>
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex items-center gap-3">
              <div className="p-3 bg-emerald-50 text-emerald-600 rounded-xl">
                <User className="w-5 h-5" />
              </div>
              <div>
                <span className="text-[11px] font-bold text-gray-400 block uppercase">総獲得リード</span>
                <span className="text-xl font-black font-mono">{totals.total_contacts.toLocaleString()}</span>
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex items-center gap-3">
              <div className="p-3 bg-amber-50 text-amber-600 rounded-xl">
                <DollarSign className="w-5 h-5" />
              </div>
              <div>
                <span className="text-[11px] font-bold text-gray-400 block uppercase">総費用</span>
                <span className="text-base font-black font-mono">{formatCurrency(totals.total_cost_jpy)}</span>
              </div>
            </div>
          </div>
        )}

        {/* 比較テーブル */}
        {rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-gray-400 gap-1">
            <Info className="w-8 h-8 text-gray-300" />
            <p className="text-sm">イベントがまだ登録されていません。</p>
          </div>
        ) : (
          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 font-bold text-sm">イベント比較</div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead>
                  <tr className="bg-gray-50 border-b border-gray-200 text-[10px] text-gray-400 font-bold uppercase">
                    <th className="px-4 py-3">イベント名</th>
                    <th className="px-4 py-3">開催日</th>
                    <th className="px-4 py-3">種別</th>
                    <th className="px-4 py-3">ステータス</th>
                    <th className="px-4 py-3 text-right">来場数</th>
                    <th className="px-4 py-3 text-right">獲得リード</th>
                    <th className="px-4 py-3 text-right">費用</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {rows.map((row) => (
                    <tr key={row.event_id} className="hover:bg-gray-50/50">
                      <td className="px-4 py-3.5 font-bold text-gray-900">{row.name}</td>
                      <td className="px-4 py-3.5 text-gray-400 font-mono text-xs">{row.event_date}</td>
                      <td className="px-4 py-3.5 text-gray-500 text-xs">{row.event_type}</td>
                      <td className="px-4 py-3.5">
                        <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${getStatusBadgeClass(row.status)}`}>
                          {row.status}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 text-right font-mono">{row.total_visitors_to_booth.toLocaleString()}</td>
                      <td className="px-4 py-3.5 text-right font-mono">{row.total_contacts_collected.toLocaleString()}</td>
                      <td className="px-4 py-3.5 text-right font-mono font-bold text-gray-900">{formatCurrency(row.cost_total_jpy)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── イベントデータパネル ──────────────────────────────────────────────────────

export default function EventDataPanel({ selectedEventId }: { selectedEventId: string | null }) {
  // イベント詳細データ
  const [eventDetail, setEventDetail] = useState<EventDetail | null>(null);
  const [eventKpi, setEventKpi] = useState<EventKPI | null>(null);
  const [eventCosts, setEventCosts] = useState<CostItem[]>([]);
  const [costSummary, setCostSummary] = useState<CostSummary | null>(null);
  const [eventSurvey, setEventSurvey] = useState<SurveyResponse | null>(null);
  const [eventBatches, setEventBatches] = useState<BatchSummary[]>([]);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // 画面タブ選択 ("data" | "lineage")
  const [activeTab, setActiveTab] = useState<"data" | "lineage">("data");

  // データビュー内のタブ選択
  const [dataSubTab, setDataSubTab] = useState<"info" | "kpi" | "costs" | "survey" | "batches">("info");

  // 来歴タブで選択されているバッチ
  const [selectedBatchId, setSelectedBatchId] = useState<string>("");
  const [loadingLineage, setLoadingLineage] = useState(false);
  const [lineageReports, setLineageReports] = useState<LineageReport[]>([]);
  const [lineageContacts, setLineageContacts] = useState<Contact[]>([]);

  // ドロワー（判定根拠詳細）表示用ステート
  const [activeDrawerReport, setActiveDrawerReport] = useState<LineageReport | null>(null);
  const [drawerMode, setDrawerMode] = useState<"transformations" | "skipped">("transformations");

  // ── イベント詳細情報のフェッチ ──────────────────────────────────────────────
  const fetchEventDetails = useCallback(async (eventId: string) => {
    setLoadingDetail(true);
    try {
      // 1. 詳細
      const detailRes = await authFetch(`/api/events/${eventId}`);
      if (detailRes.ok) {
        setEventDetail(await detailRes.json());
      } else {
        setEventDetail(null);
      }

      // 2. KPI
      const kpiRes = await authFetch(`/api/events/${eventId}/kpi`);
      if (kpiRes.ok) {
        const data = await kpiRes.json();
        setEventKpi(data.kpi);
      } else {
        setEventKpi(null);
      }

      // 3. 費用
      const costRes = await authFetch(`/api/events/${eventId}/costs`);
      if (costRes.ok) {
        const data = await costRes.json();
        setEventCosts(data.costs ?? []);
        setCostSummary(data.summary);
      } else {
        setEventCosts([]);
        setCostSummary(null);
      }

      // 4. アンケート
      const surveyRes = await authFetch(`/api/events/${eventId}/survey`);
      if (surveyRes.ok) {
        const data = await surveyRes.json();
        setEventSurvey(data.survey);
      } else {
        setEventSurvey(null);
      }

      // 5. バッチ一覧
      const batchRes = await authFetch(`/api/integration/batches?event_id=${eventId}`);
      if (batchRes.ok) {
        const data = await batchRes.json();
        const batches = data.batches ?? [];
        setEventBatches(batches);
        if (batches.length > 0) {
          setSelectedBatchId(batches[0].batch_id);
        } else {
          setSelectedBatchId("");
        }
      } else {
        setEventBatches([]);
        setSelectedBatchId("");
      }
    } catch (e) {
      console.error("Failed to fetch event details", e);
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    if (selectedEventId) {
      setActiveTab("data");
      setDataSubTab("info");
      fetchEventDetails(selectedEventId);
    }
  }, [selectedEventId, fetchEventDetails]);

  // ── バッチごとの来歴レポートとコンタクト一覧フェッチ ───────────────────────────
  const fetchBatchLineage = useCallback(async (batchId: string) => {
    if (!batchId) {
      setLineageReports([]);
      setLineageContacts([]);
      return;
    }
    setLoadingLineage(true);
    try {
      // 1. 来歴レポート
      const reportRes = await authFetch(`/api/integration/batches/${batchId}/report`);
      if (reportRes.ok) {
        const data = await reportRes.json();
        setLineageReports(data.reports ?? []);
      } else {
        setLineageReports([]);
      }

      // 2. 取り込みコンタクト一覧
      const contactsRes = await authFetch(`/api/integration/batches/${batchId}/contacts`);
      if (contactsRes.ok) {
        const data = await contactsRes.json();
        setLineageContacts(data.contacts ?? []);
      } else {
        setLineageContacts([]);
      }
    } catch (e) {
      console.error("Failed to fetch batch lineage", e);
    } finally {
      setLoadingLineage(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === "lineage" && selectedBatchId) {
      fetchBatchLineage(selectedBatchId);
    }
  }, [activeTab, selectedBatchId, fetchBatchLineage]);

  return (
    <div className="flex flex-col h-full overflow-hidden bg-white text-gray-800 relative">
      {!selectedEventId ? (
        <AllEventsSummary />
      ) : loadingDetail && !eventDetail ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        <>
          {/* メインヘッダー: イベント基本情報 ＆ メインタブ */}
          <div className="px-6 py-4 border-b border-gray-200 bg-gray-50/60 shrink-0">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${getStatusBadgeClass(eventDetail?.status ?? "")}`}>
                    {eventDetail?.status}
                  </span>
                  <span className="text-xs text-gray-400 font-mono">ID: {eventDetail?.event_id}</span>
                </div>
                <h1 className="text-lg font-extrabold text-gray-900 leading-tight">{eventDetail?.name}</h1>
              </div>

              {/* メインタブ切替 */}
              <div className="flex bg-gray-200 p-0.5 rounded-lg text-xs font-semibold">
                <button
                  onClick={() => setActiveTab("data")}
                  className={`px-4 py-1.5 rounded-md transition ${
                    activeTab === "data" ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-900"
                  }`}
                >
                  データ確認
                </button>
                <button
                  onClick={() => setActiveTab("lineage")}
                  className={`px-4 py-1.5 rounded-md transition ${
                    activeTab === "lineage" ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-900"
                  }`}
                >
                  データ来歴 (Lineage)
                </button>
              </div>
            </div>
          </div>

          {/* コンテンツエリア */}
          <div className="flex-1 overflow-hidden">
            {/* TAB 1: データ確認 (Data Viewer) */}
            {activeTab === "data" && (
              <div className="h-full flex flex-col">
                {/* サブタブナビゲーション */}
                <div className="px-6 border-b border-gray-200 shrink-0 flex gap-4 bg-white text-xs font-semibold text-gray-500">
                  {(["info", "kpi", "costs", "survey", "batches"] as const).map((tab) => {
                    const labels = {
                      info: "イベント概要",
                      kpi: "KPI実績",
                      costs: "費用明細",
                      survey: "アンケート集計",
                      batches: "取り込み履歴",
                    };
                    return (
                      <button
                        key={tab}
                        onClick={() => setDataSubTab(tab)}
                        className={`py-3 border-b-2 transition ${
                          dataSubTab === tab
                            ? "border-brand-600 text-brand-700 font-bold"
                            : "border-transparent hover:text-gray-700 hover:border-gray-200"
                        }`}
                      >
                        {labels[tab]}
                      </button>
                    );
                  })}
                </div>

                {/* サブタブ詳細 */}
                <div className="flex-1 overflow-y-auto px-6 py-6">
                  {/* A. イベント概要 */}
                  {dataSubTab === "info" && eventDetail && (
                    <div className="max-w-3xl space-y-6">
                      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
                        <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 font-bold text-sm">基本スペック</div>
                        <table className="w-full text-sm">
                          <tbody>
                            <tr className="border-b border-gray-100">
                              <td className="px-4 py-3 bg-gray-50/50 text-gray-500 w-1/3 font-semibold">イベント種別</td>
                              <td className="px-4 py-3">{eventDetail.event_type}</td>
                            </tr>
                            <tr className="border-b border-gray-100">
                              <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">会場 / 開催場所</td>
                              <td className="px-4 py-3">{eventDetail.venue || "未設定"}</td>
                            </tr>
                            <tr className="border-b border-gray-100">
                              <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">会期</td>
                              <td className="px-4 py-3 font-mono">
                                {eventDetail.event_date} 〜 {eventDetail.event_date_end}
                              </td>
                            </tr>
                            <tr className="border-b border-gray-100">
                              <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">小間番号 (Booth)</td>
                              <td className="px-4 py-3 font-mono">{eventDetail.booth_number || "未設定"}</td>
                            </tr>
                            <tr className="border-b border-gray-100">
                              <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">出展総予算 (Budget)</td>
                              <td className="px-4 py-3 font-semibold text-emerald-700">{formatCurrency(eventDetail.total_budget)}</td>
                            </tr>
                            <tr>
                              <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">目標獲得リード数</td>
                              <td className="px-4 py-3 font-mono">{eventDetail.target_contact_count} 名</td>
                            </tr>
                          </tbody>
                        </table>
                      </div>

                      {eventDetail.description && (
                        <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm space-y-2">
                          <h3 className="font-bold text-sm text-gray-500 uppercase tracking-wider flex items-center gap-1.5">
                            <FileText className="w-4 h-4 text-gray-400" />
                            概要・文脈メモ (Description)
                          </h3>
                          <p className="text-sm text-gray-600 whitespace-pre-wrap leading-relaxed leading-6 bg-gray-50 p-3.5 rounded-lg border border-gray-100">
                            {eventDetail.description}
                          </p>
                        </div>
                      )}
                    </div>
                  )}

                  {/* B. KPI実績 */}
                  {dataSubTab === "kpi" && (
                    <div className="max-w-4xl space-y-6">
                      {!eventKpi ? (
                        <div className="flex flex-col items-center justify-center py-12 text-gray-400 gap-1">
                          <Info className="w-8 h-8 text-gray-300" />
                          <p className="text-sm">このイベントのKPI実績データはまだ登録されていません。</p>
                        </div>
                      ) : (
                        <>
                          {/* サマリ数値カード */}
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex items-center gap-4">
                              <div className="p-3 bg-blue-50 text-blue-600 rounded-xl">
                                <Layers className="w-5 h-5" />
                              </div>
                              <div>
                                <span className="text-[11px] font-bold text-gray-400 block uppercase">ブース来場数</span>
                                <span className="text-xl font-black font-mono">{eventKpi.total_visitors_to_booth?.toLocaleString()} 名</span>
                              </div>
                            </div>
                            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex items-center gap-4">
                              <div className="p-3 bg-emerald-50 text-emerald-600 rounded-xl">
                                <User className="w-5 h-5" />
                              </div>
                              <div>
                                <span className="text-[11px] font-bold text-gray-400 block uppercase">獲得リード数</span>
                                <span className="text-xl font-black font-mono">{eventKpi.total_contacts_collected?.toLocaleString()} 名</span>
                              </div>
                            </div>
                            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex items-center gap-4">
                              <div className="p-3 bg-amber-50 text-amber-600 rounded-xl">
                                <Award className="w-5 h-5" />
                              </div>
                              <div>
                                <span className="text-[11px] font-bold text-gray-400 block uppercase">アポ獲得数</span>
                                <span className="text-xl font-black font-mono">{eventKpi.appointments_booked?.toLocaleString()} 名</span>
                              </div>
                            </div>
                          </div>

                          {/* その他KPI明細 */}
                          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
                            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 font-bold text-sm">詳細 KPI 指標</div>
                            <table className="w-full text-sm">
                              <tbody className="divide-y divide-gray-100">
                                <tr>
                                  <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">デモ実施数</td>
                                  <td className="px-4 py-3 font-mono">{eventKpi.demo_sessions_held} 件</td>
                                </tr>
                                <tr>
                                  <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">フォローメール開封率</td>
                                  <td className="px-4 py-3 font-mono">{(eventKpi.follow_email_open_rate * 100).toFixed(1)}%</td>
                                </tr>
                                <tr>
                                  <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">フォローメール返信率</td>
                                  <td className="px-4 py-3 font-mono">{(eventKpi.follow_email_reply_rate * 100).toFixed(1)}%</td>
                                </tr>
                                <tr>
                                  <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">見込み案件金額</td>
                                  <td className="px-4 py-3 font-semibold text-emerald-700">{formatCurrency(eventKpi.pipeline_value_jpy)}</td>
                                </tr>
                                <tr>
                                  <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">3ヶ月以内成約数</td>
                                  <td className="px-4 py-3 font-mono">{eventKpi.closed_deals_3m} 件</td>
                                </tr>
                                <tr>
                                  <td className="px-4 py-3 bg-gray-50/50 text-gray-500 font-semibold">3ヶ月以内成約金額</td>
                                  <td className="px-4 py-3 font-semibold text-emerald-700">{formatCurrency(eventKpi.closed_revenue_3m_jpy)}</td>
                                </tr>
                              </tbody>
                            </table>
                          </div>

                          {/* 温度感内訳 */}
                          {eventKpi.contacts_by_engagement && (
                            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm space-y-3">
                              <h3 className="font-bold text-sm text-gray-500 uppercase tracking-wider">獲得リード温度感内訳</h3>
                              <div className="grid grid-cols-3 gap-4 text-center">
                                <div className="bg-rose-50 border border-rose-100 rounded-lg py-2.5">
                                  <span className="text-[10px] font-bold text-rose-500 block">アポ獲得済み</span>
                                  <span className="text-lg font-extrabold text-rose-950 font-mono">{eventKpi.contacts_by_engagement.appointment_booked}</span>
                                </div>
                                <div className="bg-amber-50 border border-amber-100 rounded-lg py-2.5">
                                  <span className="text-[10px] font-bold text-amber-600 block">アポなし・感度高</span>
                                  <span className="text-lg font-extrabold text-amber-950 font-mono">{eventKpi.contacts_by_engagement.high_intent}</span>
                                </div>
                                <div className="bg-gray-50 border border-gray-200 rounded-lg py-2.5">
                                  <span className="text-[10px] font-bold text-gray-500 block">通常リード</span>
                                  <span className="text-lg font-extrabold text-gray-900 font-mono">{eventKpi.contacts_by_engagement.nurturing}</span>
                                </div>
                              </div>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}

                  {/* C. 費用明細 */}
                  {dataSubTab === "costs" && (
                    <div className="max-w-4xl space-y-6">
                      {eventCosts.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-12 text-gray-400 gap-1">
                          <Info className="w-8 h-8 text-gray-300" />
                          <p className="text-sm">このイベントの費用明細データはまだ登録されていません。</p>
                        </div>
                      ) : (
                        <>
                          {/* 総額表示 */}
                          {costSummary && (
                            <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm flex items-center justify-between">
                              <div className="flex items-center gap-3">
                                <div className="p-3 bg-emerald-50 text-emerald-600 rounded-xl">
                                  <DollarSign className="w-5 h-5" />
                                </div>
                                <div>
                                  <span className="text-[11px] font-bold text-gray-400 block uppercase">実績経費総額</span>
                                  <span className="text-2xl font-black text-emerald-800 font-mono">{formatCurrency(costSummary.total_jpy)}</span>
                                </div>
                              </div>
                              {eventDetail && (
                                <div className="text-right">
                                  <span className="text-[11px] font-bold text-gray-400 block">予算消化率</span>
                                  <span className={`text-sm font-bold ${costSummary.total_jpy > eventDetail.total_budget ? "text-rose-600" : "text-gray-600"}`}>
                                    {eventDetail.total_budget > 0
                                      ? `${((costSummary.total_jpy / eventDetail.total_budget) * 100).toFixed(1)}%`
                                      : "0.0%"}
                                  </span>
                                </div>
                              )}
                            </div>
                          )}

                          {/* 費用カテゴリ内訳カード */}
                          {costSummary && Object.keys(costSummary.by_category).length > 0 && (
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                              {Object.entries(costSummary.by_category).map(([cat, amt]) => (
                                <div key={cat} className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-center">
                                  <span className="text-[10px] text-gray-400 font-semibold block truncate" title={cat}>{cat}</span>
                                  <span className="text-xs font-bold text-gray-700 font-mono">{formatCurrency(amt)}</span>
                                </div>
                              ))}
                            </div>
                          )}

                          {/* 経費明細テーブル */}
                          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
                            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 font-bold text-sm">経費請求明細</div>
                            <div className="overflow-x-auto">
                              <table className="w-full text-sm text-left">
                                <thead>
                                  <tr className="bg-gray-50 border-b border-gray-200 text-[10px] text-gray-400 font-bold uppercase">
                                    <th className="px-4 py-3">カテゴリ</th>
                                    <th className="px-4 py-3">費用の説明</th>
                                    <th className="px-4 py-3">発注先 (Vendor)</th>
                                    <th className="px-4 py-3">請求日付</th>
                                    <th className="px-4 py-3 text-right">金額 (税込)</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100">
                                  {eventCosts.map((cost) => (
                                    <tr key={cost.cost_id} className="hover:bg-gray-50/50">
                                      <td className="px-4 py-3.5">
                                        <span className="text-[11px] bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full font-medium">
                                          {cost.category}
                                        </span>
                                      </td>
                                      <td className="px-4 py-3.5 text-gray-700 font-medium">{cost.description}</td>
                                      <td className="px-4 py-3.5 text-gray-500 font-mono">{cost.vendor_name || "—"}</td>
                                      <td className="px-4 py-3.5 text-gray-400 font-mono">{cost.invoice_date || "—"}</td>
                                      <td className="px-4 py-3.5 text-right font-bold font-mono text-gray-900">
                                        {formatCurrency(cost.amount_jpy)}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                  )}

                  {/* D. アンケート集計 */}
                  {dataSubTab === "survey" && (
                    <div className="max-w-4xl space-y-6">
                      {!eventSurvey ? (
                        <div className="flex flex-col items-center justify-center py-12 text-gray-400 gap-1">
                          <Info className="w-8 h-8 text-gray-300" />
                          <p className="text-sm">このイベントのアンケートデータはまだ登録されていません。</p>
                        </div>
                      ) : (
                        <>
                          {/* NPS / 回答数サマリ */}
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm flex items-center justify-between">
                              <div className="flex items-center gap-3">
                                <div className="p-3 bg-purple-50 text-purple-600 rounded-xl">
                                  <Smile className="w-5 h-5" />
                                </div>
                                <div>
                                  <span className="text-[11px] font-bold text-gray-400 block uppercase">NPS スコア</span>
                                  <span className={`text-2xl font-black font-mono ${eventSurvey.nps_score >= 20 ? "text-emerald-600" : eventSurvey.nps_score < 0 ? "text-rose-600" : "text-amber-500"}`}>
                                    {eventSurvey.nps_score > 0 ? `+${eventSurvey.nps_score.toFixed(1)}` : eventSurvey.nps_score.toFixed(1)}
                                  </span>
                                </div>
                              </div>
                              <div className="text-right">
                                <span className="text-[11px] font-bold text-gray-400 block">総アンケート回答数</span>
                                <span className="text-base font-extrabold text-gray-600 font-mono">{eventSurvey.total_responses} 件</span>
                              </div>
                            </div>

                            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex flex-col justify-center space-y-2">
                              <span className="text-[10px] font-bold text-gray-400 block">NPS 内訳 (推奨者 / 中立者 / 批判者)</span>
                              <div className="flex h-3.5 rounded-full overflow-hidden w-full font-mono text-[9px] text-white font-extrabold">
                                <div
                                  className="bg-emerald-500 flex items-center justify-center transition-all duration-300"
                                  style={{ width: `${(eventSurvey.nps_promoters / eventSurvey.total_responses) * 100}%` }}
                                  title={`推奨者: ${eventSurvey.nps_promoters}人`}
                                >
                                  {eventSurvey.nps_promoters > 0 && "推奨"}
                                </div>
                                <div
                                  className="bg-amber-400 flex items-center justify-center transition-all duration-300"
                                  style={{ width: `${(eventSurvey.nps_passives / eventSurvey.total_responses) * 100}%` }}
                                  title={`中立者: ${eventSurvey.nps_passives}人`}
                                >
                                  {eventSurvey.nps_passives > 0 && "中立"}
                                </div>
                                <div
                                  className="bg-rose-500 flex items-center justify-center transition-all duration-300"
                                  style={{ width: `${(eventSurvey.nps_detractors / eventSurvey.total_responses) * 100}%` }}
                                  title={`批判者: ${eventSurvey.nps_detractors}人`}
                                >
                                  {eventSurvey.nps_detractors > 0 && "批判"}
                                </div>
                              </div>
                              <div className="flex justify-between text-[10px] text-gray-400 font-medium">
                                <span>推奨: {eventSurvey.nps_promoters}人</span>
                                <span>中立: {eventSurvey.nps_passives}人</span>
                                <span>批判: {eventSurvey.nps_detractors}人</span>
                              </div>
                            </div>
                          </div>

                          {/* カテゴリ別満足度 */}
                          {eventSurvey.satisfaction_scores && eventSurvey.satisfaction_scores.length > 0 && (
                            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm space-y-3">
                              <h3 className="font-bold text-sm text-gray-500 uppercase tracking-wider">項目別満足度スコア (5段階評価)</h3>
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                {eventSurvey.satisfaction_scores.map((score, i) => (
                                  <div key={i} className="flex items-center justify-between border-b border-gray-100 pb-2">
                                    <span className="text-xs font-semibold text-gray-700">{score.category}</span>
                                    <div className="flex items-center gap-2">
                                      <div className="w-24 bg-gray-100 h-2 rounded-full overflow-hidden">
                                        <div className="bg-brand-500 h-full" style={{ width: `${(score.avg_score / 5) * 100}%` }} />
                                      </div>
                                      <span className="text-xs font-bold font-mono text-gray-900">{score.avg_score.toFixed(2)}</span>
                                      <span className="text-[10px] text-gray-400 font-mono">({score.response_count}名)</span>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* 定性フリーコメント */}
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div className="bg-emerald-50/50 border border-emerald-100 rounded-xl p-4 shadow-sm space-y-3">
                              <h4 className="font-bold text-xs text-emerald-800 uppercase tracking-wider flex items-center gap-1.5">
                                <Smile className="w-3.5 h-3.5 text-emerald-600" />
                                ポジティブな声
                              </h4>
                              <ul className="space-y-2 text-xs text-emerald-950">
                                {eventSurvey.verbatim_positives && eventSurvey.verbatim_positives.length > 0 ? (
                                  eventSurvey.verbatim_positives.map((cmt, i) => (
                                    <li key={i} className="bg-white/80 p-2.5 rounded-lg border border-emerald-100/50 leading-relaxed font-medium">
                                      {cmt}
                                    </li>
                                  ))
                                ) : (
                                  <p className="text-gray-400 italic">特になし</p>
                                )}
                              </ul>
                            </div>

                            <div className="bg-rose-50/50 border border-rose-100 rounded-xl p-4 shadow-sm space-y-3">
                              <h4 className="font-bold text-xs text-rose-800 uppercase tracking-wider flex items-center gap-1.5">
                                <Info className="w-3.5 h-3.5 text-rose-600" />
                                ネガティブ・課題点
                              </h4>
                              <ul className="space-y-2 text-xs text-rose-950">
                                {eventSurvey.verbatim_negatives && eventSurvey.verbatim_negatives.length > 0 ? (
                                  eventSurvey.verbatim_negatives.map((cmt, i) => (
                                    <li key={i} className="bg-white/80 p-2.5 rounded-lg border border-rose-100/50 leading-relaxed font-medium">
                                      {cmt}
                                    </li>
                                  ))
                                ) : (
                                  <p className="text-gray-400 italic">特になし</p>
                                )}
                              </ul>
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                  )}

                  {/* E. 取り込みバッチ履歴 */}
                  {dataSubTab === "batches" && (
                    <div className="max-w-4xl space-y-6">
                      {eventBatches.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-12 text-gray-400 gap-1">
                          <Info className="w-8 h-8 text-gray-300" />
                          <p className="text-sm">このイベントに対するデータ取り込み履歴はありません。</p>
                        </div>
                      ) : (
                        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
                          <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 font-bold text-sm">取り込み履歴一覧</div>
                          <div className="overflow-x-auto">
                            <table className="w-full text-sm text-left">
                              <thead>
                                <tr className="bg-gray-50 border-b border-gray-200 text-[10px] text-gray-400 font-bold uppercase">
                                  <th className="px-4 py-3">バッチID</th>
                                  <th className="px-4 py-3">取り込み日時</th>
                                  <th className="px-4 py-3">ステータス</th>
                                  <th className="px-4 py-3">ファイル名</th>
                                  <th className="px-4 py-3 text-right">生成エンティティ</th>
                                  <th className="px-4 py-3">操作</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-gray-100">
                                {eventBatches.map((batch) => (
                                  <tr key={batch.batch_id} className="hover:bg-gray-50/50">
                                    <td className="px-4 py-3.5 font-mono text-xs text-gray-500 font-semibold">{batch.batch_id}</td>
                                    <td className="px-4 py-3.5 text-gray-400 font-mono">
                                      {new Date(batch.created_at).toLocaleString("ja-JP")}
                                    </td>
                                    <td className="px-4 py-3.5">
                                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${
                                        batch.status === "done"
                                          ? "bg-emerald-50 text-emerald-600 border border-emerald-200"
                                          : batch.status === "processing"
                                          ? "bg-amber-50 text-amber-600 border border-amber-200"
                                          : "bg-rose-50 text-rose-600 border border-rose-200"
                                      }`}>
                                        {batch.status}
                                      </span>
                                    </td>
                                    <td className="px-4 py-3.5 text-gray-700 max-w-[200px] truncate" title={batch.filenames.join(", ")}>
                                      {batch.filenames.join(", ")}
                                    </td>
                                    <td className="px-4 py-3.5 text-right font-mono text-xs text-gray-600">
                                      {batch.created_entities && Object.keys(batch.created_entities).length > 0 ? (
                                        Object.entries(batch.created_entities).map(([k, v]) => `${k}:${v}`).join(", ")
                                      ) : (
                                        "—"
                                      )}
                                    </td>
                                    <td className="px-4 py-3.5">
                                      <button
                                        onClick={() => {
                                          setSelectedBatchId(batch.batch_id);
                                          setActiveTab("lineage");
                                        }}
                                        className="text-xs text-brand-600 hover:text-brand-800 font-bold underline"
                                      >
                                        来歴を確認
                                      </button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* TAB 2: データ来歴 (Data Lineage) */}
            {activeTab === "lineage" && (
              <div className="h-full flex flex-col overflow-hidden">
                {/* バッチセレクターヘッダー */}
                <div className="px-6 py-3 border-b border-gray-200 shrink-0 flex items-center justify-between bg-white bg-gray-50/50">
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-bold text-gray-500 uppercase">確認する取り込みバッチ:</span>
                    <select
                      value={selectedBatchId}
                      onChange={(e) => setSelectedBatchId(e.target.value)}
                      className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-brand-500 bg-white font-mono font-medium"
                    >
                      {eventBatches.map((batch) => (
                        <option key={batch.batch_id} value={batch.batch_id}>
                          {batch.batch_id} ({batch.filenames.join(", ")})
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* 来歴詳細コンテンツ */}
                <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
                  {loadingLineage ? (
                    <div className="flex justify-center py-16">
                      <div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
                    </div>
                  ) : lineageReports.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-16 text-gray-400 gap-1">
                      <Info className="w-8 h-8 text-gray-300" />
                      <p className="text-sm">来歴データがありません。</p>
                    </div>
                  ) : (
                    lineageReports.map((report, idx) => (
                      <div key={idx} className="space-y-6 border-b border-gray-100 pb-6 last:border-0 last:pb-0">
                        {/* 4ステップフロー図 (来歴プロセスの視覚化) */}
                        <div className="bg-gray-50/70 border border-gray-200/80 rounded-2xl p-6 shadow-sm">
                          <div className="flex items-center gap-2 mb-4">
                            <Activity className="w-4 h-4 text-brand-600" />
                            <h3 className="font-bold text-xs text-gray-500 uppercase tracking-wider">
                              データ統合プロセス来歴 ({report.source.filename})
                            </h3>
                          </div>

                          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 items-stretch relative">
                            {/* コネクション線（大画面用） */}
                            <div className="hidden lg:block absolute top-1/2 left-[23%] w-[4%] h-0.5 bg-gray-200 -translate-y-1/2" />
                            <div className="hidden lg:block absolute top-1/2 left-[48%] w-[4%] h-0.5 bg-gray-200 -translate-y-1/2" />
                            <div className="hidden lg:block absolute top-1/2 left-[73%] w-[4%] h-0.5 bg-gray-200 -translate-y-1/2" />

                            {/* STEP 1: ファイルソース */}
                            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex flex-col justify-between hover:border-gray-300 transition">
                              <div>
                                <div className="flex items-center gap-1 text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
                                  <span>STEP 1</span>
                                  <span>•</span>
                                  <span>入力ソース</span>
                                </div>
                                <div className="flex items-center gap-2">
                                  <FileSpreadsheet className="w-8 h-8 text-emerald-600 shrink-0" />
                                  <div className="min-w-0">
                                    <p className="text-xs font-bold text-gray-900 truncate" title={report.source.filename}>
                                      {report.source.filename}
                                    </p>
                                    <span className="text-[10px] text-gray-400 capitalize">
                                      {report.source.source_type === "tabular" ? "表形式 (CSV/Excel)" : "非構造化ドキュメント (Text)"}
                                    </span>
                                  </div>
                                </div>
                              </div>
                              <div className="text-[10px] text-gray-400 mt-4 border-t border-gray-100 pt-2 font-mono">
                                作成: {new Date(report.source.created_at).toLocaleDateString()}
                              </div>
                            </div>

                            {/* STEP 2: AI一次判定 */}
                            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex flex-col justify-between hover:border-gray-300 transition">
                              <div>
                                <div className="flex items-center gap-1 text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
                                  <span>STEP 2</span>
                                  <span>•</span>
                                  <span>AI一次判定 (Stage 1)</span>
                                </div>
                                {report.source.source_type === "tabular" && report.stage1_ai.column_mapping ? (
                                  <div className="space-y-1.5">
                                    <p className="text-xs font-bold text-gray-800">
                                      カラム自動マッピング: {report.stage1_ai.column_mapping.entity_type}
                                    </p>
                                    <div className="text-[10px] text-gray-500 bg-gray-50 px-2 py-1.5 rounded border border-gray-100 max-h-16 overflow-y-auto font-mono">
                                      {Object.entries(report.stage1_ai.column_mapping.column_map).map(([k, v]) => (
                                        <div key={k} className="truncate">
                                          "{k}" ➔ {v}
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                ) : report.stage1_ai.raw_extraction ? (
                                  <div className="space-y-1">
                                    <p className="text-xs font-bold text-gray-800">非構造化テキスト情報抽出</p>
                                    <div className="text-[9px] font-mono text-gray-500 bg-gray-50 p-2 rounded border border-gray-100 max-h-20 overflow-y-auto">
                                      {JSON.stringify(report.stage1_ai.raw_extraction, null, 2)}
                                    </div>
                                  </div>
                                ) : (
                                  <p className="text-xs text-gray-400 italic">一次判定結果がありません</p>
                                )}
                              </div>
                              <div className="text-[10px] text-brand-600 mt-3 border-t border-gray-100 pt-2 font-semibold flex items-center gap-0.5">
                                <CheckCircle2 className="w-3.5 h-3.5" />
                                Gemini-3.1-Flash-Lite
                              </div>
                            </div>

                            {/* STEP 3: Python加工処理 */}
                            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex flex-col justify-between border-amber-200 bg-amber-50/10 hover:border-amber-300 transition">
                              <div>
                                <div className="flex items-center gap-1 text-[10px] font-bold text-amber-600 uppercase tracking-wider mb-2">
                                  <span>STEP 3</span>
                                  <span>•</span>
                                  <span>Python加工判定 (Stage 2)</span>
                                </div>
                                <div className="space-y-2">
                                  <div className="text-xs font-bold text-gray-700">決定論的ロジック</div>
                                  <div className="text-[10px] text-gray-600 space-y-1">
                                    <div>• 加工判定件数: {report.stage2_transformations.transformations.length} 件</div>
                                    <div className={report.stage2_transformations.skipped_records.length > 0 ? "text-rose-600 font-bold" : "text-gray-500"}>
                                      • スキップ件数: {report.stage2_transformations.skipped_records.length} 行
                                    </div>
                                  </div>
                                </div>
                              </div>

                              <div className="mt-3 border-t border-gray-100 pt-2">
                                <button
                                  onClick={() => {
                                    setActiveDrawerReport(report);
                                    setDrawerMode("transformations");
                                  }}
                                  className="w-full text-center text-xs bg-amber-500 text-white rounded-lg py-1.5 font-bold hover:bg-amber-600 transition"
                                >
                                  判定根拠を確認 (Auditable)
                                </button>
                              </div>
                            </div>

                            {/* STEP 4: 最終オントロジーエンティティ */}
                            <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex flex-col justify-between hover:border-gray-300 transition">
                              <div>
                                <div className="flex items-center gap-1 text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
                                  <span>STEP 4</span>
                                  <span>•</span>
                                  <span>オントロジー格納</span>
                                </div>
                                <div className="space-y-2">
                                  <p className="text-xs font-bold text-emerald-800">Firestoreへ型保存完了</p>
                                  <div className="flex flex-wrap gap-1">
                                    {report.created_entity_ids && Object.keys(report.created_entity_ids).length > 0 ? (
                                      Object.entries(report.created_entity_ids).map(([k, ids]) => (
                                        <span key={k} className="text-[9px] bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-full border border-emerald-100 font-semibold font-mono">
                                          {k}: {ids.length}
                                        </span>
                                      ))
                                    ) : (
                                      <span className="text-[10px] text-gray-400 italic">エンティティ生成なし</span>
                                    )}
                                  </div>
                                </div>
                              </div>
                              <div className="text-[10px] text-gray-400 mt-4 border-t border-gray-100 pt-2 font-mono">
                                完了ステータス: Done
                              </div>
                            </div>
                          </div>
                        </div>

                        {/* バッチコンタクト一覧テーブル */}
                        {lineageContacts.length > 0 && (
                          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
                            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 font-bold text-xs text-gray-500">
                              <span>このバッチで取り込まれたコンタクト一覧 ({lineageContacts.length}名)</span>
                            </div>
                            <div className="overflow-x-auto">
                              <table className="w-full text-xs text-left">
                                <thead>
                                  <tr className="bg-gray-50 border-b border-gray-200 text-[10px] text-gray-400 font-bold uppercase">
                                    <th className="px-4 py-2.5">氏名</th>
                                    <th className="px-4 py-2.5">会社名 / 部署</th>
                                    <th className="px-4 py-2.5">役職</th>
                                    <th className="px-4 py-2.5">温度感 (Engagement)</th>
                                    <th className="px-4 py-2.5">関心製品</th>
                                    <th className="px-4 py-2.5">抽出された課題</th>
                                    <th className="px-4 py-2.5">メール</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100">
                                  {lineageContacts.map((contact) => (
                                    <tr key={contact.contact_id} className="hover:bg-gray-50/50">
                                      <td className="px-4 py-3 font-bold text-gray-900">{contact.name}</td>
                                      <td className="px-4 py-3">
                                        <div className="font-semibold text-gray-700">{contact.company_name}</div>
                                        <div className="text-[10px] text-gray-400">{contact.department || "—"}</div>
                                      </td>
                                      <td className="px-4 py-3 text-gray-500">{contact.job_title || "—"}</td>
                                      <td className="px-4 py-3">
                                        {contact.engagement_level ? (
                                          <span className={`px-2 py-0.5 rounded-full font-bold text-[9px] ${
                                            contact.engagement_level === "アポ獲得済み"
                                              ? "bg-rose-50 text-rose-600 border border-rose-100"
                                              : contact.engagement_level === "アポなし・感度高"
                                              ? "bg-amber-50 text-amber-600 border border-amber-100"
                                              : "bg-gray-50 text-gray-500 border border-gray-200"
                                          }`}>
                                            {contact.engagement_level}
                                          </span>
                                        ) : (
                                          "—"
                                        )}
                                      </td>
                                      <td className="px-4 py-3">
                                        <div className="flex flex-wrap gap-0.5">
                                          {contact.interested_products && contact.interested_products.length > 0 ? (
                                            contact.interested_products.map((p, k) => (
                                              <span key={k} className="bg-blue-50 text-blue-700 px-1 rounded text-[9px] border border-blue-100 font-semibold">
                                                {p}
                                              </span>
                                            ))
                                          ) : (
                                            <span className="text-gray-400 italic">—</span>
                                          )}
                                        </div>
                                      </td>
                                      <td className="px-4 py-3 text-gray-600 max-w-[200px] truncate" title={contact.extracted_challenge}>
                                        {contact.extracted_challenge || "—"}
                                      </td>
                                      <td className="px-4 py-3 text-gray-400 font-mono text-[10px]">{contact.email || "—"}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {/* 詳細判定根拠ドロワー (スライドインサイドパネル - Auditable AIの可視化) */}
      {activeDrawerReport && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-xs transition-opacity duration-300">
          <div className="absolute inset-0" onClick={() => setActiveDrawerReport(null)} />

          <div className="relative w-full max-w-2xl bg-white h-full shadow-2xl flex flex-col z-10">
            {/* ヘッダー */}
            <div className="px-6 py-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
              <div>
                <span className="text-[10px] text-gray-400 font-bold block">AUDITABLE AI PROCESS REPORT</span>
                <h3 className="text-base font-extrabold text-gray-800">
                  加工処理決定（Stage 2）根拠明細
                </h3>
              </div>
              <button
                onClick={() => setActiveDrawerReport(null)}
                className="p-1.5 rounded-lg hover:bg-gray-200 transition text-gray-400 hover:text-gray-600"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 内訳切替タブ */}
            <div className="px-6 border-b border-gray-100 shrink-0 flex gap-4 bg-white text-xs font-bold text-gray-400">
              <button
                onClick={() => setDrawerMode("transformations")}
                className={`py-3.5 border-b-2 transition ${
                  drawerMode === "transformations"
                    ? "border-amber-500 text-amber-600 font-bold animate-pulse"
                    : "border-transparent hover:text-gray-600"
                }`}
              >
                判定決定レコード ({activeDrawerReport.stage2_transformations.transformations.length})
              </button>
              <button
                onClick={() => setDrawerMode("skipped")}
                className={`py-3.5 border-b-2 transition ${
                  drawerMode === "skipped"
                    ? "border-amber-500 text-amber-600 font-bold"
                    : "border-transparent hover:text-gray-600"
                } ${activeDrawerReport.stage2_transformations.skipped_records.length > 0 ? "text-rose-600" : ""}`}
              >
                スキップ除外レコード ({activeDrawerReport.stage2_transformations.skipped_records.length})
              </button>
            </div>

            {/* スクロールコンテンツ */}
            <div className="flex-1 overflow-y-auto p-6 space-y-6">
              {drawerMode === "transformations" ? (
                activeDrawerReport.stage2_transformations.transformations.length === 0 ? (
                  <p className="text-sm text-gray-400 italic text-center py-12">決定レコードがありません</p>
                ) : (
                  activeDrawerReport.stage2_transformations.transformations.map((trans, i) => (
                    <div key={i} className="bg-gray-50 border border-gray-200 rounded-xl p-4 shadow-sm space-y-3">
                      {/* 対象名 */}
                      <div className="flex items-center gap-1.5 border-b border-gray-200 pb-2">
                        <User className="w-4 h-4 text-gray-400" />
                        <span className="text-xs font-bold text-gray-500 uppercase tracking-wider">{trans.entity_type}</span>
                        <span className="text-xs font-black text-gray-800">• {trans.source_label}</span>
                      </div>

                      {/* 判断決定リスト */}
                      <div className="space-y-3.5">
                        {trans.decisions.map((dec, dIdx) => (
                          <div key={dIdx} className="bg-white border border-gray-100 rounded-lg p-3 space-y-2.5">
                            <div className="flex items-center justify-between text-xs">
                              <span className="font-semibold text-brand-600 bg-brand-50 px-2 py-0.5 rounded-md">
                                {dec.field}
                              </span>
                              <span className="font-bold text-gray-800">
                                判定値: <code className="bg-gray-100 px-1 rounded font-mono text-[11px]">{dec.value}</code>
                              </span>
                            </div>

                            {/* 生シグナル */}
                            {dec.source_signals && Object.keys(dec.source_signals).length > 0 && (
                              <div className="text-[10px] text-gray-500 bg-gray-50 p-2 rounded font-mono space-y-0.5">
                                <span className="font-bold text-gray-400 block text-[9px] uppercase">生入力シグナル</span>
                                {Object.entries(dec.source_signals).map(([sigK, sigV]) => (
                                  <div key={sigK} className="truncate">
                                    {sigK}: <span className="text-gray-800 font-semibold">"{sigV}"</span>
                                  </div>
                                ))}
                              </div>
                            )}

                            {/* 非Optional 根拠 */}
                            <div className="text-xs bg-amber-50/50 text-amber-900 border border-amber-100/50 p-2.5 rounded-lg flex items-start gap-1.5">
                              <HelpCircle className="w-3.5 h-3.5 text-amber-500 shrink-0 mt-0.5" />
                              <div className="leading-relaxed">
                                <span className="font-bold text-amber-800 block text-[10px] mb-0.5">判定根拠 (Transform Reason)</span>
                                {dec.reason}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))
                )
              ) : (
                activeDrawerReport.stage2_transformations.skipped_records.length === 0 ? (
                  <p className="text-sm text-gray-400 italic text-center py-12">スキップされたレコードはありません</p>
                ) : (
                  activeDrawerReport.stage2_transformations.skipped_records.map((skip, i) => (
                    <div key={i} className="bg-rose-50/50 border border-rose-200 rounded-xl p-4 shadow-sm space-y-2">
                      <div className="flex items-center gap-1.5 text-xs text-rose-800 font-bold border-b border-rose-100 pb-1.5">
                        <X className="w-4 h-4 text-rose-500" />
                        <span>除外された行 ({skip.entity_type})</span>
                      </div>
                      <div className="space-y-1">
                        <span className="text-[10px] text-gray-400 block font-bold uppercase">除外理由 (Reason)</span>
                        <p className="text-xs text-rose-950 font-bold leading-relaxed">{skip.reason}</p>
                      </div>
                      {skip.detail && (
                        <div className="text-[10px] font-mono text-gray-500 bg-white p-2 rounded border border-rose-100/50 max-h-24 overflow-y-auto">
                          {skip.detail}
                        </div>
                      )}
                    </div>
                  ))
                )
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
