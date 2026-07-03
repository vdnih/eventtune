import { defineConfig, devices } from "@playwright/test";

/**
 * E2E スモークテスト設定。
 *
 * Firebase エミュレータ（auth + firestore）の中で実行する前提:
 *   firebase emulators:exec --only firestore,auth --project demo-eventtune \
 *     "cd frontend && npx playwright test"
 * （リポジトリルートの npm script `e2e` 参照 — docs/TESTING.md）
 *
 * webServer がバックエンド（uvicorn, :8000）とフロントエンド（next dev, :3000）を起動する。
 * スモーク範囲は LLM を呼ばないフロー（ログイン → スペース作成 → データ閲覧）に限定しており、
 * Gemini / Agent Engine のモックは不要。
 */

const emulatorEnv = {
  GOOGLE_CLOUD_PROJECT: "demo-eventtune",
  FIREBASE_PROJECT_ID: "demo-eventtune",
  FIRESTORE_EMULATOR_HOST: process.env.FIRESTORE_EMULATOR_HOST ?? "127.0.0.1:8080",
  FIREBASE_AUTH_EMULATOR_HOST: process.env.FIREBASE_AUTH_EMULATOR_HOST ?? "127.0.0.1:9099",
};

export default defineConfig({
  testDir: "./e2e",
  // ジャーニー型のスモークなので直列実行（状態はエミュレータ内で共有される）
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // 事前インストール済み Chromium を使う環境向けの逃げ道（通常は未設定でよい）
        ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
          ? {
              launchOptions: {
                executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
              },
            }
          : {}),
      },
    },
  ],
  webServer: [
    {
      command: "uv run uvicorn main:app --port 8000",
      cwd: "../backend",
      url: "http://localhost:8000/health",
      reuseExistingServer: !process.env.CI,
      env: emulatorEnv,
      timeout: 60_000,
    },
    {
      command: "npm run dev",
      url: "http://localhost:3000/login",
      reuseExistingServer: !process.env.CI,
      env: {
        NEXT_PUBLIC_API_URL: "http://localhost:8000",
        NEXT_PUBLIC_AUTH_EMULATOR_HOST: emulatorEnv.FIREBASE_AUTH_EMULATOR_HOST,
        // Auth エミュレータ利用時、Firebase 設定はダミーでよい
        NEXT_PUBLIC_FIREBASE_API_KEY: "demo-api-key",
        NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "demo-eventtune.firebaseapp.com",
        NEXT_PUBLIC_FIREBASE_PROJECT_ID: "demo-eventtune",
        NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: "demo-eventtune.appspot.com",
        NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: "000000000000",
        NEXT_PUBLIC_FIREBASE_APP_ID: "1:000000000000:web:demo",
      },
      timeout: 120_000,
    },
  ],
});
