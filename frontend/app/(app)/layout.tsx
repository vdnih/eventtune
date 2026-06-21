"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { ChevronDown, Settings, LogOut, Users, BarChart3, Plus, Check } from "lucide-react";
import { auth } from "@/lib/firebase";
import { useAuth } from "@/hooks/useAuth";
import { SpaceProvider, useSpace } from "@/lib/space-context";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { user, state } = useAuth();

  useEffect(() => {
    if (state === "unauthed") router.replace("/login");
  }, [state, router]);

  if (state !== "authed") {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <SpaceProvider>
      <AppShell userEmail={user?.email ?? ""}>{children}</AppShell>
    </SpaceProvider>
  );
}

function AppShell({ userEmail, children }: { userEmail: string; children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { spaces, activeSpace, loading } = useSpace();

  // オンボーディング: スペース未所属なら作成画面へ誘導（spaces配下は除外して無限遷移を防ぐ）
  const onSpacesRoute = pathname.startsWith("/spaces");
  useEffect(() => {
    if (!loading && spaces.length === 0 && !onSpacesRoute) {
      router.replace("/spaces/new");
    }
  }, [loading, spaces.length, onSpacesRoute, router]);

  if (loading) {
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
          <div className="flex items-center gap-3">
            <Link href="/dashboard" className="text-lg font-bold text-brand-600">
              イベントマーケティング
            </Link>
            <SpaceSwitcher />
          </div>
          <UserMenu userEmail={userEmail} />
        </div>
      </header>
      {/* スペース切替時はサブツリーを作り直して全データを再取得する */}
      <main key={activeSpace?.space_id ?? "none"} className="flex-1 overflow-hidden">
        {children}
      </main>
    </div>
  );
}

function SpaceSwitcher() {
  const { spaces, activeSpace, switchSpace } = useSpace();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  if (!activeSpace) return null;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
      >
        <span className="font-medium text-gray-700 max-w-[160px] truncate">{activeSpace.name}</span>
        <ChevronDown className="w-4 h-4 text-gray-400" />
      </button>
      {open && (
        <div className="absolute left-0 mt-1 w-64 bg-white border border-gray-200 rounded-md shadow-lg z-50 py-1">
          {spaces.map((s) => (
            <button
              key={s.space_id}
              onClick={() => {
                switchSpace(s.space_id);
                setOpen(false);
              }}
              className="w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-gray-50 text-left"
            >
              <span className="truncate">{s.name}</span>
              {s.space_id === activeSpace.space_id && <Check className="w-4 h-4 text-brand-600" />}
            </button>
          ))}
          <div className="border-t border-gray-100 my-1" />
          <Link
            href="/spaces/new"
            onClick={() => setOpen(false)}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50 text-brand-600"
          >
            <Plus className="w-4 h-4" /> 新しいスペースを作成
          </Link>
        </div>
      )}
    </div>
  );
}

function UserMenu({ userEmail }: { userEmail: string }) {
  const router = useRouter();
  const { isOwner } = useSpace();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900"
      >
        <span className="max-w-[180px] truncate">{userEmail}</span>
        <ChevronDown className="w-4 h-4 text-gray-400" />
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-56 bg-white border border-gray-200 rounded-md shadow-lg z-50 py-1">
          <Link href="/settings/usage" onClick={() => setOpen(false)} className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50">
            <BarChart3 className="w-4 h-4 text-gray-400" /> 利用状況
          </Link>
          {isOwner && (
            <>
              <Link href="/settings/members" onClick={() => setOpen(false)} className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50">
                <Users className="w-4 h-4 text-gray-400" /> メンバー管理
              </Link>
              <Link href="/settings/space" onClick={() => setOpen(false)} className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50">
                <Settings className="w-4 h-4 text-gray-400" /> スペース設定
              </Link>
            </>
          )}
          <div className="border-t border-gray-100 my-1" />
          <button
            onClick={() => auth.signOut().then(() => router.replace("/login"))}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50 text-gray-600"
          >
            <LogOut className="w-4 h-4 text-gray-400" /> ログアウト
          </button>
        </div>
      )}
    </div>
  );
}
