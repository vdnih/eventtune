"use client";

import { ProseMarkdown, slugify } from "@/components/ProseMarkdown";
import { MANUAL_MARKDOWN } from "@/content/help/manual";

// 本文中の h2（## 見出し）から目次を組み立てる。id は ProseMarkdown と同じ slugify を使う。
const toc = MANUAL_MARKDOWN.split("\n")
  .filter((line) => line.startsWith("## "))
  .map((line) => {
    const text = line.replace(/^##\s+/, "").trim();
    return { text, id: slugify(text) };
  });

export default function HelpPage() {
  return (
    <div className="h-full overflow-auto p-8">
      <div className="mx-auto max-w-3xl">
        {toc.length > 0 && (
          <nav className="mb-8 rounded-lg border border-gray-200 bg-gray-50 p-5">
            <p className="mb-2 text-sm font-semibold text-gray-700">目次</p>
            <ul className="space-y-1">
              {toc.map((item) => (
                <li key={item.id}>
                  <a
                    href={`#${item.id}`}
                    className="text-sm text-brand-600 hover:text-brand-700 hover:underline"
                  >
                    {item.text}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        )}
        <article className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
          <ProseMarkdown content={MANUAL_MARKDOWN} />
        </article>
      </div>
    </div>
  );
}
