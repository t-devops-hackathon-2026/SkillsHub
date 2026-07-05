#!/usr/bin/env bash
# Cloud Run サービス用エントリポイント。Cloud Run が渡す $PORT で Streamlit を起動する。
# イメージの既定 CMD は migrate.sh のため、サービス側は --command=./scripts/serve.sh で
# これを指定する（イメージは1本を全ワークロードで共用する。cf. cloudbuild.yaml）。
set -euo pipefail

exec uv run --no-sync streamlit run skillshub/app/main.py \
  --server.port "${PORT:-8080}" \
  --server.address 0.0.0.0 \
  --server.headless true
