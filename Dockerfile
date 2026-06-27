# マイグレーション／シード実行用イメージ。デプロイ(#22)のアプリ実行にも流用できる。
FROM python:3.12-slim

# uv を同梱イメージからコピー（依存解決を高速・再現可能に）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 依存だけ先に入れてレイヤキャッシュを効かせる。
# build-system を持たないプロジェクト構成のため、自身(skillshub)は install せず
# ソース実行（cwd / prepend_sys_path）で動かす方針 → --no-install-project。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# アプリ本体と alembic 設定・マイグレーション・スクリプトを配置
COPY . .

# .venv の実行ファイルにパスを通す（alembic / python を直接叩けるように）
ENV PATH="/app/.venv/bin:$PATH"

# Cloud Run Job 側でも上書きするが、既定は migrate スクリプト
CMD ["./scripts/migrate.sh"]
