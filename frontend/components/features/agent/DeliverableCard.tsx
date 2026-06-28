"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Lightbulb, Link } from "lucide-react";
import { cn } from "@/lib/utils";

interface DeliverableBlock {
  block_type: string;
  reason_for_inclusion: string;
  associated_asset_ids?: string[];
  associated_content_ids?: string[];
  block_text?: string;
}

interface Deliverable {
  deliverable_id?: string;
  email_id?: string;        // 旧フィールド名との互換
  person_id?: string;
  contact_id?: string;      // 旧フィールド名との互換
  subject?: string;
  blocks: DeliverableBlock[];
  person_name?: string;
  person_company?: string;
  bucket?: string;
  contact_name?: string;    // 旧フィールド名との互換
  contact_company?: string; // 旧フィールド名との互換
  engagement_level?: string;
}

interface Props {
  deliverable: Deliverable;
  index: number;
}

export function DeliverableCard({ deliverable: d, index }: Props) {
  const [expanded, setExpanded] = useState(index === 0);
  const [showReasons, setShowReasons] = useState(false);

  const displayName = d.person_name ?? d.contact_name ?? "";
  const displayCompany = d.person_company ?? d.contact_company ?? "";
  const displaySegment = d.bucket ?? d.engagement_level ?? "";

  return (
    <div className="border border-gray-200 rounded-xl overflow-hidden bg-white shadow-sm">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-start justify-between p-4 text-left hover:bg-gray-50 transition"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-medium text-gray-400">#{index + 1}</span>
            {displayName && <span className="font-semibold text-gray-800 text-sm">{displayName}</span>}
            {displayCompany && <span className="text-gray-500 text-sm">{displayCompany}</span>}
            {displaySegment && (
              <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-gray-100 text-gray-600">
                {displaySegment}
              </span>
            )}
          </div>
          {d.subject && (
            <p className="mt-1 text-sm text-gray-700 font-medium truncate">件名: {d.subject}</p>
          )}
        </div>
        {expanded ? (
          <ChevronUp className="w-4 h-4 text-gray-400 shrink-0 mt-0.5 ml-2" />
        ) : (
          <ChevronDown className="w-4 h-4 text-gray-400 shrink-0 mt-0.5 ml-2" />
        )}
      </button>

      {expanded && (
        <div className="border-t border-gray-100 p-4 space-y-3">
          <button
            onClick={() => setShowReasons((v) => !v)}
            className="flex items-center gap-1.5 text-xs text-amber-600 hover:text-amber-700 transition"
          >
            <Lightbulb className="w-3.5 h-3.5" />
            {showReasons ? "AIの思考を隠す" : "AIの思考を見る（Chain-of-Thought）"}
          </button>

          {d.blocks.map((block, i) => (
            <div key={i} className="rounded-lg border border-gray-100 bg-gray-50 p-3 space-y-2">
              <p className="text-xs font-semibold text-brand-600">{block.block_type}</p>

              {showReasons && block.reason_for_inclusion && (
                <div className="flex gap-2 bg-amber-50 border border-amber-100 rounded-md p-2">
                  <Lightbulb className="w-3.5 h-3.5 text-amber-500 shrink-0 mt-0.5" />
                  <p className="text-xs text-amber-700 leading-relaxed">{block.reason_for_inclusion}</p>
                </div>
              )}

              {block.block_text && (
                <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{block.block_text}</p>
              )}

              {(() => {
                const ids = block.associated_asset_ids ?? block.associated_content_ids ?? [];
                return ids.length > 0 ? (
                  <div className="flex items-center gap-1 flex-wrap">
                    <Link className="w-3 h-3 text-gray-400" />
                    {ids.map((id) => (
                      <span key={id} className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded font-mono">
                        {id}
                      </span>
                    ))}
                  </div>
                ) : null;
              })()}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
