import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "EventTune",
  description: "AIによる個別カスタマイズメール一括生成",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
