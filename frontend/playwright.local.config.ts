import { defineConfig, devices } from "@playwright/test";

/**
 * ローカル専用の Playwright 設定 — 実 Gemini 呼び出しを伴う画面（データ取り込み等）を
 * 手元のブラウザで確認するためのもの。playwright.config.ts（CI の e2e-smoke）とは
 * 意図的に分離している:
 *
 *   - playwright.config.ts: GOOGLE_CLOUD_PROJECT も demo- プレフィックスに固定し、
 *     実クラウドへの誤接続を構造的に防ぐ（ADR方針）。そのため LLM を呼ぶフローは扱えない。
 *   - この設定: Firestore/Auth だけをエミュレータへ逃がし（データは汚さない）、
 *     Vertex AI 呼び出しはバックエンドの backend/.env に書かれた実プロジェクトを
 *     そのまま使う。AI が絡む機能を手元で目視確認したいときに使う。
 *
 * 使い方:
 *   1. 別ターミナルで Firestore/Auth エミュレータを起動しておく
 *        firebase emulators:start --only firestore,auth --project demo-eventtune
 *   2. frontend/ で
 *        npm run e2e:local:live-ai -- e2e/foo.spec.ts
 *
 * webServer は reuseExistingServer 固定（変更を試すたびに再起動不要にするため）。
 * バックエンドのコードを変更したら、起動済みの uvicorn --reload が自動で拾う。
 */

const emulatorEnv = {
  FIRESTORE_EMULATOR_HOST: process.env.FIRESTORE_EMULATOR_HOST ?? "127.0.0.1:8080",
  FIREBASE_AUTH_EMULATOR_HOST: process.env.FIREBASE_AUTH_EMULATOR_HOST ?? "127.0.0.1:9099",
  // Firestore/Auth 側のプロジェクトIDはエミュレータ内のみで完結するダミーでよい。
  // GOOGLE_CLOUD_PROJECT はあえて上書きしない = backend/.env の実プロジェクトのまま
  // Vertex AI を呼ぶ（firebase_admin の projectId と genai の project は別変数なので分離できる）。
  FIREBASE_PROJECT_ID: "demo-eventtune",
};

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "uv run uvicorn main:app --reload --port 8000",
      cwd: "../backend",
      url: "http://localhost:8000/health",
      reuseExistingServer: true,
      env: { ...process.env, ...emulatorEnv },
      timeout: 60_000,
    },
    {
      command: "npm run dev",
      url: "http://localhost:3000/login",
      reuseExistingServer: true,
      env: {
        NEXT_PUBLIC_API_URL: "http://localhost:8000",
        NEXT_PUBLIC_AUTH_EMULATOR_HOST: emulatorEnv.FIREBASE_AUTH_EMULATOR_HOST,
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
