#!/usr/bin/env bash
# DB スキーマ適用＋初期シード投入をまとめて実行する（Rails の rake db:migrate + db:seed 相当）。
# ローカルでも `./scripts/migrate.sh`、staging では Cloud Run Job のコマンドとして同じものを使う。
# 接続先は DATABASE_URL（ローカルは .env、デプロイ環境は Secret Manager）を参照する。
set -euo pipefail

# uv run 経由で実行することで、venv を activate していないシェルからでも Docker 内でも動く。
# --no-sync は「既にある .venv をそのまま使う」指定。実行時に依存の再解決・再インストールを
# 走らせない（Cloud Run Job などネットワークに出たくない実行環境で必須）。
echo "==> alembic upgrade head"
uv run --no-sync alembic upgrade head

echo "==> seed (冪等)"
uv run --no-sync python -m skillshub.db.seed

echo "==> done"
