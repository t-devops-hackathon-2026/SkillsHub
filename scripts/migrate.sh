#!/usr/bin/env bash
# DB スキーマ適用＋初期シード投入をまとめて実行する（Rails の rake db:migrate + db:seed 相当）。
# ローカルでも `./scripts/migrate.sh`、staging では Cloud Run Job のコマンドとして同じものを使う。
# 接続先は DATABASE_URL（ローカルは .env、デプロイ環境は Secret Manager）を参照する。
set -euo pipefail

echo "==> alembic upgrade head"
alembic upgrade head

echo "==> seed (冪等)"
python -m skillshub.db.seed

echo "==> done"
