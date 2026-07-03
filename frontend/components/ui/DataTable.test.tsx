/**
 * 汎用データテーブルのレンダリングテスト（RTL パターンの雛形）。
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DataTable } from "./DataTable";

const rows = [
  { person_id: "p1", name: "山田太郎", email: "yamada@example.com" },
  { person_id: "p2", name: "佐藤花子" },
];

describe("DataTable", () => {
  it("空のときは空状態メッセージを表示する", () => {
    render(<DataTable rows={[]} selectedIndex={null} onSelectRow={() => {}} />);
    expect(screen.getByText("データがありません")).toBeInTheDocument();
  });

  it("和集合カラムのヘッダと全行を描画する", () => {
    render(<DataTable rows={rows} selectedIndex={null} onSelectRow={() => {}} />);
    for (const col of ["person_id", "name", "email"]) {
      expect(screen.getByRole("columnheader", { name: col })).toBeInTheDocument();
    }
    expect(screen.getByText("山田太郎")).toBeInTheDocument();
    expect(screen.getByText("佐藤花子")).toBeInTheDocument();
    // 欠損セルは — 表示
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("行クリックで onSelectRow が行indexで呼ばれる", async () => {
    const onSelectRow = vi.fn();
    render(<DataTable rows={rows} selectedIndex={null} onSelectRow={onSelectRow} />);
    await userEvent.click(screen.getByText("佐藤花子"));
    expect(onSelectRow).toHaveBeenCalledWith(1);
  });
});
