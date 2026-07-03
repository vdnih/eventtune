"use client";

import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * フルページの読み物（利用規約・プライバシーポリシー・ヘルプ）向け Markdown レンダラ。
 *
 * チャットバブル用の MessageMarkdown は色を親から継承する設計のため、
 * 記事ページでは明示的な文字色・余白を持つこちらを使う。
 * h2/h3 には見出しテキストから生成した id を付与し、目次アンカーに使えるようにする。
 */

/** 子要素からプレーンテキストを取り出す（見出しの id 生成用）。 */
function toText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(toText).join("");
  if (typeof node === "object" && "props" in node) {
    return toText((node as { props?: { children?: ReactNode } }).props?.children);
  }
  return "";
}

/** 見出しテキストを URL アンカー用の slug に変換する。 */
export function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[\s　]+/g, "-")
    .replace(/[^\w぀-ヿ一-鿿-]/g, "");
}

const components: Components = {
  h1: ({ children }) => (
    <h1 className="mt-0 mb-6 text-2xl font-bold text-gray-900">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2
      id={slugify(toText(children))}
      className="mt-10 mb-3 scroll-mt-20 border-b border-gray-100 pb-1 text-lg font-bold text-gray-900"
    >
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3
      id={slugify(toText(children))}
      className="mt-6 mb-2 scroll-mt-20 text-base font-semibold text-gray-900"
    >
      {children}
    </h3>
  ),
  p: ({ children }) => <p className="my-3 leading-7 text-gray-700">{children}</p>,
  ul: ({ children }) => (
    <ul className="my-3 ml-6 list-disc space-y-1.5 text-gray-700">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-3 ml-6 list-decimal space-y-1.5 text-gray-700">{children}</ol>
  ),
  li: ({ children }) => <li className="leading-7">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-gray-900">{children}</strong>,
  a: ({ href, children }) => {
    const internal = href?.startsWith("/");
    return (
      <a
        href={href}
        {...(internal ? {} : { target: "_blank", rel: "noreferrer" })}
        className="text-brand-600 underline underline-offset-2 hover:text-brand-700"
      >
        {children}
      </a>
    );
  },
  blockquote: ({ children }) => (
    <blockquote className="my-4 rounded-r-md border-l-4 border-brand-200 bg-brand-50/50 px-4 py-2 text-sm text-gray-600">
      {children}
    </blockquote>
  ),
  code: ({ className, children }) => {
    const isBlock = (className ?? "").includes("language-");
    if (isBlock) return <code className={className}>{children}</code>;
    return (
      <code className="rounded bg-gray-100 px-1 py-0.5 font-mono text-sm text-gray-800">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="my-4 overflow-x-auto rounded-lg bg-gray-900 p-4 font-mono text-sm text-gray-100">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="my-4 overflow-x-auto">
      <table className="w-full border-collapse text-sm text-gray-700">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-gray-50">{children}</thead>,
  th: ({ children }) => (
    <th className="border border-gray-200 px-3 py-2 text-left font-semibold text-gray-900">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-gray-200 px-3 py-2 align-top">{children}</td>
  ),
  hr: () => <hr className="my-8 border-gray-200" />,
};

export function ProseMarkdown({ content }: { content: string }) {
  return (
    <div className="break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
