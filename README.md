# SkillsHub — 社内 Skills ダッシュボード

> [DevOps AI Agent ハッカソン 2026](https://findy.notion.site/devops-ai-agent-hackathon-2026) 出展作品

社内に散在する AI エージェント用 Skills を自動収集し、検索・品質評価・改善提案まで一気通貫で行うダッシュボード。
AI エージェントが「司書」として Skills の鮮度を維持し続ける。

## アーキテクチャ

![アーキテクチャ図](docs/designs/overview/architecture.png)

## 主な機能

- GitHub リポジトリから Skills を自動収集・分類
- 品質スコアリングと鮮度（fresh / stale / needs_update）の可視化
- 自然言語による Skills 検索
- AI による改善提案（diff 形式）と重複検出

## 技術スタック

| レイヤー | 技術 |
|---|---|
| フロントエンド | Streamlit |
| バックエンド | Python 3.12 / SQLAlchemy / Alembic |
| LLM / 埋め込み | Gemini（google-genai） / Vertex AI |
| データベース | PostgreSQL + pgvector |
| インフラ | Cloud Run / Cloud SQL / Secret Manager |
| CI/CD | Cloud Build |

## セットアップ

```bash
cp .env.example .env
docker compose up -d
uv sync
./scripts/migrate.sh
```

詳細は [docs/local-development.md](docs/local-development.md) を参照。

## ドキュメント

- [ローカル開発ガイド](docs/local-development.md)
- [デプロイ手順・インフラ構成](docs/deploy.md)
- [設計ドキュメント](docs/designs/overview/overview.md)
