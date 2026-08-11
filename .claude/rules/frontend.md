---
paths:
  - "frontend/**"
---

# Frontend (Next.js / TypeScript)

## コマンド（`frontend/` で実行）

```bash
npm install
npm run dev
npm run lint          # next lint
npm run typecheck     # tsc --noEmit
npm run test          # vitest run
npm run e2e           # playwright test
npm run e2e:local     # firebase emulators 経由（スクリプト自体がルートに cd してエミュレータを起動する）
```

## 実装上の注意

- API クライアント（`lib/api.ts`）を経由しない直接 fetch を書かない。全呼び出しに `Authorization` と `X-Space-Id` を付与する契約がある（`lib/api.test.ts` が保証）。
- E2E（`e2e/`）はエミュレータ限定の匿名テストログインを使う。Google ポップアップは apis.google.com 依存で CI・プロキシ環境で不安定なため使わない。
- E2E は主要導線の生存確認のみに留める。LLM を呼ぶフローは対象外（Gemini のモックを不要にして構成を軽く保つ方針）。
