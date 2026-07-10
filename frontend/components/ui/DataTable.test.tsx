/**
 * 汎用データテーブルのレンダリングテスト（RTL パターンの雛形）。
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DataTable } from "./DataTable";
import type { DisplayRow } from "@/app/(app)/data/useTableView";

const rows = [
  { person_id: "p1", name: "山田太郎", email: "yamada@example.com" },
  { person_id: "p2", name: "佐藤花子" },
];
const displayRows: DisplayRow[] = rows.map((row, index) => ({ row, index }));
const columns = ["person_id", "name", "email"];

function baseProps() {
  return {
    displayRows,
    columns,
    selectedIndex: null,
    onSelectRow: vi.fn(),
    sort: null,
    onSort: vi.fn(),
    filters: {},
    onFilter: vi.fn(),
  };
}

describe("DataTable", () => {
  it("列が無いときは空状態メッセージを表示する", () => {
    render(<DataTable {...baseProps()} columns={[]} />);
    expect(screen.getByText("データがありません")).toBeInTheDocument();
  });

  it("列ヘッダ（ソートボタン）と全行を描画する", () => {
    render(<DataTable {...baseProps()} />);
    for (const col of columns) {
      expect(screen.getByRole("button", { name: col })).toBeInTheDocument();
    }
    expect(screen.getByText("山田太郎")).toBeInTheDocument();
    expect(screen.getByText("佐藤花子")).toBeInTheDocument();
    // 欠損セルは — 表示
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("行クリックで onSelectRow が元の index で呼ばれる", async () => {
    const props = baseProps();
    render(<DataTable {...props} />);
    await userEvent.click(screen.getByText("佐藤花子"));
    expect(props.onSelectRow).toHaveBeenCalledWith(1);
  });

  it("ヘッダクリックで onSort が列キーで呼ばれる", async () => {
    const props = baseProps();
    render(<DataTable {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "name" }));
    expect(props.onSort).toHaveBeenCalledWith("name");
  });

  it("該当行が無いときはメッセージ行を出す", () => {
    render(<DataTable {...baseProps()} displayRows={[]} />);
    expect(screen.getByText("該当するデータがありません")).toBeInTheDocument();
  });
});
