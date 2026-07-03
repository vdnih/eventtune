/**
 * 中央 API クライアント（authFetch / authHeaders）の回帰テスト。
 *
 * すべてのバックエンド呼び出しに Authorization と X-Space-Id が付くこと、
 * アクティブスペースの localStorage 連携が正しいことを固定する。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getIdToken = vi.fn();

vi.mock("@/lib/firebase", () => ({
  auth: {
    get currentUser() {
      return { getIdToken };
    },
  },
}));

import { API_BASE, authFetch, authHeaders, getActiveSpaceId, setActiveSpaceId } from "./api";

beforeEach(() => {
  getIdToken.mockResolvedValue("token-123");
  setActiveSpaceId(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("authHeaders", () => {
  it("Authorization ヘッダに Firebase ID トークンを付与する", async () => {
    const headers = await authHeaders();
    expect(headers.Authorization).toBe("Bearer token-123");
  });

  it("アクティブスペースがあるときだけ X-Space-Id を付与する", async () => {
    expect((await authHeaders())["X-Space-Id"]).toBeUndefined();
    setActiveSpaceId("space_abc");
    expect((await authHeaders())["X-Space-Id"]).toBe("space_abc");
  });

  it("追加ヘッダをマージする", async () => {
    const headers = await authHeaders({ "Content-Type": "application/json" });
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers.Authorization).toBe("Bearer token-123");
  });
});

describe("authFetch", () => {
  it("API_BASE を前置し認証ヘッダ付きで fetch する", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    setActiveSpaceId("space_abc");

    await authFetch("/api/data/persons", { method: "GET" });

    expect(fetchMock).toHaveBeenCalledWith(
      `${API_BASE}/api/data/persons`,
      expect.objectContaining({
        method: "GET",
        headers: expect.objectContaining({
          Authorization: "Bearer token-123",
          "X-Space-Id": "space_abc",
        }),
      }),
    );
  });
});

describe("setActiveSpaceId", () => {
  it("localStorage に保持し、null でクリアする", () => {
    setActiveSpaceId("space_xyz");
    expect(getActiveSpaceId()).toBe("space_xyz");
    expect(window.localStorage.getItem("activeSpaceId")).toBe("space_xyz");

    setActiveSpaceId(null);
    expect(getActiveSpaceId()).toBeNull();
    expect(window.localStorage.getItem("activeSpaceId")).toBeNull();
  });
});
