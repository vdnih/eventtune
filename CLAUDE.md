# EventTune (marketing-mail-generator)

イベントマーケAIエージェント。カオスなマーケティングデータを OSI オントロジー（Pydantic SSoT）に統合し、AIエージェントが個別フォローメール生成・振り返り分析を行うマルチテナント SaaS。全体像は `README.md` を参照。

**このファイルには、ドキュメントへの導線と落とし穴だけを書く。詳細は各 docs にある。**

## ドキュメント

| 知りたいこと | 参照先 |
|---|---|
| 何ができるか・アーキテクチャ概要 | `README.md` |
| テスト戦略・実行方法・保証する不変条件 | `docs/TESTING.md` |
| システム思想・命名規約 | `docs/PHILOSOPHY_AND_NAMING.md` |
| ソフトウェア設計（オントロジー・エージェント構成・APIルート） | `docs/SOFTWARE_ARCHITECTURE.md` |
| インフラ構成（GCP/Firebase/Cloud Run） | `docs/INFRA_ARCHITECTURE.md` |
| 過去の設計判断とその理由 | `docs/ADR.md`（ADR-001〜018。ADR-019 以降は `docs/adr/` に追加する） |
| マーケティング設計思想 | `docs/MARKETING_PHILOSOPHY.md` |

backend / frontend それぞれのコマンド・実装規約は `.claude/rules/backend.md` / `.claude/rules/frontend.md` を参照（該当ディレクトリのファイルを開いたときだけ読み込まれる）。

## 知らないと事故る注意点

- ディレクトリ名は `marketing-mail-generator` だが GitHub repo 名は `vdnih/eventtune`（プロダクト名改称の経緯は ADR-014）。
- `demo-` プレフィックスの Firebase project ID（`demo-eventtune`）はエミュレータ専用。本番への誤接続を構造的に防ぐための命名で、変更しないこと。
- LLM 出力の品質評価（「良いメールが生成されるか」）は意図的に CI スコープ外。ADK Runner / Vertex AI Agent Engine も CI では動かさない（詳細は `docs/TESTING.md`）。
