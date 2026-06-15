"use client";

import { useEffect, useState } from "react";
import { onAuthStateChanged } from "firebase/auth";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { auth } from "@/lib/firebase";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [authState, setAuthState] = useState<"checking" | "authed" | "unauthed">("checking");

  useEffect(() => {
    const unsub = onAuthStateChanged(auth, (user) => {
      if (!user) {
        setAuthState("unauthed");
        router.replace("/login");
      } else {
        setAuthState("authed");
      }
    });
    return () => unsub();
  }, [router]);

  if (authState !== "authed") {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <header className="shrink-0 bg-white border-b border-gray-200">
        <div className="px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-8">
            <span className="text-lg font-bold text-brand-600 mr-2">
              イベントマーケティング
            </span>
            <nav className="flex gap-6 h-14">
              <Link
                href="/dashboard"
                className={`inline-flex items-center px-1 border-b-2 text-sm font-medium transition ${
                  pathname === "/dashboard"
                    ? "border-brand-600 text-gray-900"
                    : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
                }`}
              >
                エージェント
              </Link>
            </nav>
          </div>
          <button
            onClick={() => auth.signOut().then(() => router.replace("/login"))}
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            ログアウト
          </button>
        </div>
      </header>
      <main className="flex-1 overflow-hidden">{children}</main>
    </div>
  );
}
