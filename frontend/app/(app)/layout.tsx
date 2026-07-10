"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { ChevronDown, Settings, LogOut, Users, BarChart3, Plus, Check, HelpCircle, AlertTriangle } from "lucide-react";
import { auth } from "@/lib/firebase";
import { useAuth } from "@/hooks/useAuth";
import { SpaceProvider, useSpace } from "@/lib/space-context";
import { authFetch } from "@/lib/api";
import { ConsentGate } from "@/components/ConsentGate";
import { HACKATHON_NOTICE } from "@/lib/legal";

function FullScreenSpinner() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { user, state } = useAuth();

  useEffect(() => {
    if (state === "unauthed") router.replace("/login");
  }, [state, router]);

  if (state !== "authed") {
    return <FullScreenSpinner />;
  }

  return (
    <ConsentBoundary>
      <SpaceProvider>
        <AppShell userEmail={user?.email ?? ""}>{children}</AppShell>
      </SpaceProvider>
    </ConsentBoundary>
  );
}

// 認証後・アプリ本体表示前に利用規約への同意を確認するゲート。
// 同意（またはバージョン一致）が確認できるまでスペース取得やオンボーディングへ進めない。
function ConsentBoundary({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<"checking" | "needed" | "ok" | "error">("checking");
  const [version, setVersion] = useState("");

  const check = useCallback(async () => {
    setStatus("checking");
    try {
      const res = await authFetch("/api/users/me");
      if (!res.ok) throw new Error("failed to load user");
      const me = await res.json();
      setVersion(me.current_terms_version ?? "");
      setStatus(
        me.terms_accepted_version && me.terms_accepted_version === me.current_terms_version
          ? "ok"
          : "needed",
      );
    } catch {
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  if (status === "checking") return <FullScreenSpinner />;
  if (status === "error") {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 p-4">
        <p className="text-sm text-gray-600">読み込みに失敗しました。</p>
        <button
          onClick={check}
          className="px-4 py-2 rounded-lg bg-brand-600 text-white text-sm font-medium hover:bg-brand-700"
        >
          再試行
        </button>
      </div>
    );
  }
  if (status === "needed") {
    return <ConsentGate version={version} onAccepted={() => setStatus("ok")} />;
  }
  return <>{children}</>;
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
    return <FullScreenSpinner />;
  }

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <div className="shrink-0 flex items-center justify-center gap-2 bg-amber-50 border-b border-amber-200 px-4 py-1.5 text-xs text-amber-900">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
        <span className="text-center">{HACKATHON_NOTICE}</span>
      </div>
      <header className="shrink-0 bg-white border-b border-gray-200">
        <div className="px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/agent" className="text-lg font-bold text-brand-600">
              EventTune
            </Link>
            <SpaceSwitcher />
            <nav className="flex items-center gap-1 text-sm">
              <NavLink href="/agent" label="エージェント" />
              <NavLink href="/data" label="データ" />
            </nav>
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

function NavLink({ href, label }: { href: string; label: string }) {
  const pathname = usePathname();
  const active = pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      className={cn(
        "px-2.5 py-1.5 rounded-md text-sm",
        active ? "bg-brand-50 text-brand-700 font-medium" : "text-gray-600 hover:bg-gray-50"
      )}
    >
      {label}
    </Link>
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
          <Link href="/help" onClick={() => setOpen(false)} className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50">
            <HelpCircle className="w-4 h-4 text-gray-400" /> ヘルプ / 使い方
          </Link>
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
          <div className="border-t border-gray-100 my-1" />
          <div className="flex items-center gap-2 px-3 py-1.5 text-xs text-gray-400">
            <Link href="/terms" onClick={() => setOpen(false)} className="hover:text-gray-600">
              利用規約
            </Link>
            <span>·</span>
            <Link href="/privacy" onClick={() => setOpen(false)} className="hover:text-gray-600">
              プライバシー
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
