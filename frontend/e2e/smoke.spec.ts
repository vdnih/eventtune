/**
 * E2E スモーク: 主要導線がエンドツーエンドで生きていることの最小検証。
 *
 * ログイン（Auth エミュレータの匿名テストユーザー）→ 利用規約同意 →
 * スペース作成（実バックエンド + Firestore エミュレータ）→ データ閲覧。
 *
 * Google ポップアップ経由のログインは apis.google.com への到達が必要で
 * CI/プロキシ環境で脆いため、エミュレータ限定のテストログインを使う。
 * LLM を呼ぶフローは扱わない（構成を軽く保つ。docs/TESTING.md 参照）。
 */
import { expect, test, type Page } from "@playwright/test";

test.describe.configure({ mode: "serial" });

async function signInAsTestUser(page: Page) {
  await page.goto("/login");
  await page
    .getByRole("button", { name: "テストユーザーでログイン（エミュレータ）" })
    .click();
}

test("未認証アクセスはログイン画面へリダイレクトされる", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("button", { name: "Googleでログイン" })).toBeVisible();
});

test("ログイン → 同意 → スペース作成 → データ閲覧", async ({ page }) => {
  await signInAsTestUser(page);

  // 初回ユーザーは利用規約の同意ゲートを通過する
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "同意してはじめる" }).click();

  // スペース未所属のためオンボーディング（作成画面）へ自動誘導される
  await expect(page).toHaveURL(/\/spaces\/new/, { timeout: 15_000 });

  await page.getByPlaceholder("例: マーケティング部").fill("E2E スモークスペース");
  await page.getByRole("button", { name: "スペースを作成" }).click();

  // 作成後はダッシュボード（チャット）へ。スペース名がヘッダに反映される
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 });
  await expect(page.getByText("E2E スモークスペース").first()).toBeVisible();
  await expect(page.getByText("AIエージェントです", { exact: false })).toBeVisible();

  // データエクスプローラが開き、ビュー一覧（バックエンドの /api/data/collections）が出る
  await page.goto("/explorer");
  await expect(page.getByText("ハウスリスト")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("イベント", { exact: true }).first()).toBeVisible();
});
