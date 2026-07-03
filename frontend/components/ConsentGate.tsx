"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { authFetch } from "@/lib/api";
import { HACKATHON_NOTICE } from "@/lib/legal";

/**
 * 利用規約・プライバシーポリシーへの同意ゲート。
 *
 * 認証後・アプリ本体の表示前に挟み、未同意（またはバージョン改定後）のユーザーに表示する。
 * チェックボックスにチェックしない限り同意ボタンは押せない（既存の submit ガター慣習）。
 * 同意すると /api/users/me/accept-terms に version を POST して記録し、onAccepted を呼ぶ。
 * 規約・プライバシーへのリンクは別タブで開き、この画面（＝認証状態）から離脱させない。
 */
export function ConsentGate({
  version,
  onAccepted,
}: {
  version: string;
  onAccepted: () => void;
}) {
  const [agreed, setAgreed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleAccept() {
    setSubmitting(true);
    setError("");
    try {
      const res = await authFetch("/api/users/me/accept-terms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version }),
      });
      if (!res.ok) throw new Error("accept-terms failed");
      onAccepted();
    } catch {
      setError("同意の記録に失敗しました。時間をおいて再度お試しください。");
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-brand-50 to-blue-100 p-4">
      <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-md">
        <h1 className="text-xl font-bold text-gray-900 mb-2">ご利用の前に</h1>
        <p className="text-sm text-gray-600 mb-4">
          EventTune をご利用いただくには、利用規約およびプライバシーポリシーへの同意が必要です。
          内容をご確認のうえ、同意してください。
        </p>

        <div className="mb-6 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
          <span>{HACKATHON_NOTICE}</span>
        </div>

        <div className="rounded-lg border border-gray-200 divide-y divide-gray-100 mb-5">
          <Link
            href="/terms"
            target="_blank"
            rel="noreferrer"
            className="block px-4 py-3 text-sm text-brand-600 hover:bg-gray-50"
          >
            利用規約を読む（別タブで開く）
          </Link>
          <Link
            href="/privacy"
            target="_blank"
            rel="noreferrer"
            className="block px-4 py-3 text-sm text-brand-600 hover:bg-gray-50"
          >
            プライバシーポリシーを読む（別タブで開く）
          </Link>
        </div>

        <label className="flex items-start gap-3 mb-5 cursor-pointer">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
          />
          <span className="text-sm text-gray-700">
            利用規約およびプライバシーポリシーに同意します
          </span>
        </label>

        {error && (
          <p className="text-red-500 text-sm mb-4 bg-red-50 p-3 rounded-lg">{error}</p>
        )}

        <button
          onClick={handleAccept}
          disabled={!agreed || submitting}
          className="w-full px-4 py-3 rounded-lg bg-brand-600 text-white font-medium hover:bg-brand-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {submitting ? "処理中..." : "同意してはじめる"}
        </button>
      </div>
    </div>
  );
}
