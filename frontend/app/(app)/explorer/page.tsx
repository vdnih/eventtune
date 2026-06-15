"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// データエクスプローラーはダッシュボードに統合された。
// 旧URL（ブックマーク等）はダッシュボードへリダイレクトする。
export default function ExplorerRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/dashboard");
  }, [router]);

  return (
    <div className="flex-1 flex items-center justify-center h-full">
      <div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}
