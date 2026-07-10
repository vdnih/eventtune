"use client";

/**
 * IngestionPlanCard — 取り込みプランのマーケター向け表示
 *
 * 「ファイル / 取り込む内容 / 読み取った項目」の簡易テーブルを主表示にし、内部フィールド名
 * （column_map の生の対応関係）はエンジニア・データ担当者向けとして折りたたみに格納する。
 */
import { useState } from "react";
import { ENTITY_LABEL, LINK_KIND_LABEL, type BatchPlan, type FilePlan } from "@/lib/ingestion";

const MAX_COLUMNS_SHOWN = 5;

function summarizeColumns(fp: FilePlan): string {
  const cols: string[] = [];
  for (const t of fp.targets) {
    for (const k of Object.keys(t.column_map)) {
      if (!cols.includes(k)) cols.push(k);
    }
  }
  if (cols.length === 0) return "—";
  const shown = cols.slice(0, MAX_COLUMNS_SHOWN).join("、");
  const more = cols.length > MAX_COLUMNS_SHOWN ? ` 他${cols.length - MAX_COLUMNS_SHOWN}項目` : "";
  return `${shown}${more}`;
}

function summarizeKinds(fp: FilePlan): string {
  const labels = fp.targets.map((t) => ENTITY_LABEL[t.entity_type] ?? t.entity_type);
  const uniq = [...new Set(labels)];
  return uniq.length > 0 ? uniq.join("・") : "（対象なし）";
}

export function IngestionPlanCard({ plan }: { plan: BatchPlan }) {
  const [detailOpen, setDetailOpen] = useState(false);
  const warnings = plan.files.flatMap((fp) =>
    [fp.unmapped_notes, fp.extraction_caveat]
      .filter((note): note is string => Boolean(note))
      .map((note) => ({ filename: fp.filename, note }))
  );

  if (plan.files.length === 0) {
    return <p className="text-xs text-gray-400">取り込み対象のファイルがありません。</p>;
  }

  return (
    <div className="text-xs">
      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="w-full border-collapse text-xs">
          <thead className="bg-gray-100">
            <tr>
              <th className="border border-gray-200 px-2 py-1.5 text-left font-semibold text-gray-600">
                ファイル
              </th>
              <th className="border border-gray-200 px-2 py-1.5 text-left font-semibold text-gray-600">
                取り込む内容
              </th>
              <th className="border border-gray-200 px-2 py-1.5 text-left font-semibold text-gray-600">
                読み取った項目
              </th>
            </tr>
          </thead>
          <tbody>
            {plan.files.map((fp) => (
              <tr key={fp.filename}>
                <td className="border border-gray-200 px-2 py-1.5 align-top text-gray-700">
                  {fp.filename}
                </td>
                <td className="border border-gray-200 px-2 py-1.5 align-top text-gray-700">
                  {summarizeKinds(fp)}
                </td>
                <td className="border border-gray-200 px-2 py-1.5 align-top text-gray-700">
                  {summarizeColumns(fp)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {warnings.length > 0 && (
        <div className="mt-1.5 space-y-0.5">
          {warnings.map((w, i) => (
            <div key={i} className="text-amber-700">
              ⚠️ {w.filename}: {w.note}
            </div>
          ))}
        </div>
      )}

      <button
        onClick={() => setDetailOpen((v) => !v)}
        className="mt-2 flex items-center gap-1 text-gray-400 hover:text-gray-600 transition"
      >
        <span>詳細を見る（データ担当者向け）</span>
        <span>{detailOpen ? "▲" : "▼"}</span>
      </button>

      {detailOpen && (
        <div className="mt-1.5 space-y-3 rounded-lg bg-gray-100/60 p-2.5">
          {plan.files.map((fp) => (
            <div key={fp.filename} className="space-y-1">
              <div className="font-semibold text-gray-600">{fp.filename}</div>
              {fp.business_context && <div className="text-gray-500">{fp.business_context}</div>}
              {fp.targets.map((t, i) => (
                <div key={i} className="space-y-0.5 pl-2">
                  <div className="text-gray-500">
                    種別: {ENTITY_LABEL[t.entity_type] ?? t.entity_type}
                  </div>
                  {Object.keys(t.column_map).length > 0 && (
                    <div className="text-gray-500">
                      カラム対応:{" "}
                      {Object.entries(t.column_map)
                        .map(([k, v]) => `${k}→${v}`)
                        .join("、")}
                    </div>
                  )}
                  {Object.entries(t.link_columns).map(([kind, col]) => (
                    <div key={kind} className="text-gray-500">
                      {LINK_KIND_LABEL[kind] ?? kind}リンク: 「{col}」列で行ごとに解決
                    </div>
                  ))}
                  {Object.entries(t.column_modes).filter(([, mode]) => mode === "ai_parse").length >
                    0 && (
                    <div className="text-gray-500">
                      AI解釈列:{" "}
                      {Object.entries(t.column_modes)
                        .filter(([, mode]) => mode === "ai_parse")
                        .map(([col]) => col)
                        .join("、")}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
