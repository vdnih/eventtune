import Link from "next/link";
import type { Metadata } from "next";
import { ProseMarkdown } from "@/components/ProseMarkdown";
import { PRIVACY_MARKDOWN } from "@/content/legal/privacy";
import { PRIVACY_LAST_UPDATED } from "@/lib/legal";

export const metadata: Metadata = {
  title: "プライバシーポリシー | EventTune",
};

export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-6">
          <Link href="/" className="text-lg font-bold text-brand-600">
            EventTune
          </Link>
          <Link href="/terms" className="text-sm text-gray-500 hover:text-gray-900">
            利用規約
          </Link>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-6 py-10">
        <div className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm sm:p-10">
          <ProseMarkdown content={PRIVACY_MARKDOWN} />
          <p className="mt-8 border-t border-gray-100 pt-4 text-xs text-gray-400">
            最終更新日：{PRIVACY_LAST_UPDATED}
          </p>
        </div>
      </main>
    </div>
  );
}
