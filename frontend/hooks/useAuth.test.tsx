/**
 * useAuth の認証状態遷移テスト。
 *
 * onAuthStateChanged の購読/解除と checking → authed/unauthed の遷移を固定する。
 */
import { act, renderHook } from "@testing-library/react";
import type { User } from "firebase/auth";
import { beforeEach, describe, expect, it, vi } from "vitest";

const onAuthStateChanged = vi.fn();

vi.mock("firebase/auth", () => ({
  onAuthStateChanged: (...args: unknown[]) => onAuthStateChanged(...args),
}));

vi.mock("@/lib/firebase", () => ({ auth: {} }));

import { useAuth } from "./useAuth";

describe("useAuth", () => {
  let emit: (user: User | null) => void;
  const unsubscribe = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    onAuthStateChanged.mockImplementation((_auth: unknown, cb: (u: User | null) => void) => {
      emit = cb;
      return unsubscribe;
    });
  });

  it("初期状態は checking", () => {
    const { result } = renderHook(() => useAuth());
    expect(result.current.state).toBe("checking");
    expect(result.current.user).toBeNull();
  });

  it("ユーザー通知で authed になる", () => {
    const { result } = renderHook(() => useAuth());
    const fakeUser = { uid: "uid_1" } as User;
    act(() => emit(fakeUser));
    expect(result.current.state).toBe("authed");
    expect(result.current.user).toBe(fakeUser);
  });

  it("null 通知で unauthed になる", () => {
    const { result } = renderHook(() => useAuth());
    act(() => emit(null));
    expect(result.current.state).toBe("unauthed");
  });

  it("アンマウントで購読を解除する", () => {
    const { unmount } = renderHook(() => useAuth());
    unmount();
    expect(unsubscribe).toHaveBeenCalledOnce();
  });
});
