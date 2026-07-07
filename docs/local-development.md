# ローカル開発

ローカル開発では、ローカルに立てた DB（pgvector 入り PostgreSQL）を使う。staging などのリモート DB には接続しない。

## セットアップ

```bash
cp .env.example .env
docker compose up -d
uv sync
./scripts/migrate.sh
```

> `scripts/migrate.sh` は `alembic upgrade head` と `python -m skillshub.db.seed`（手動 Skill 2件＋空 Repository 1件・冪等）を順に流すラッパ。個別に流したい場合は `uv run alembic upgrade head` / `uv run python -m skillshub.db.seed` を直接実行してもよい。staging では同じ `migrate.sh` を Cloud Run Job のコマンドとして使う。手動 Skill 2件は架空のデモデータのため、本番・staging では環境変数 `SEED_DEMO_SKILLS=0` を設定して投入をスキップする。

## 接続確認

```bash
uv run python -c "from skillshub.shared.db import get_session; s=next(get_session()); s.execute(__import__('sqlalchemy').text('select 1')); print('DB OK')"
```

`DB OK` が出力されればローカル DB への接続成功。

## Gemini の認証

LLM・埋め込みの呼び出しは google-genai SDK 経由で、認証は環境変数で切り替わる。

- ローカル: `.env` に `GOOGLE_API_KEY`（Gemini API キー）を設定する。
- デプロイ環境: API キーを使わず、`GOOGLE_GENAI_USE_VERTEXAI=TRUE`・`GOOGLE_CLOUD_PROJECT`・`GOOGLE_CLOUD_LOCATION` を設定してサービスアカウントの ADC で Vertex AI を呼ぶ。`GOOGLE_CLOUD_LOCATION` は `global` を指定する（Gemini 3 系モデルは global エンドポイントのみの提供）。

## アクセス制限（パスワードゲート）

環境変数 `APP_PASSWORD` を設定するとアプリ表示前にパスワード入力を求める（未設定のローカル開発ではゲートは出ない）。デプロイ時は Secret Manager に登録し環境変数として渡す。一度ログインすると署名付きクッキーにより7日間はリロードしても再入力不要。

## スキーマ／マイグレーション

- スキーマの正は ORM モデル [`skillshub/shared/models.py`](../skillshub/shared/models.py)（カラム定義の出典は [`docs/designs/step1/er.md`](designs/step1/er.md)）。
- マイグレーションは Alembic（`skillshub/db/migrations/`）。接続先は `alembic.ini` ではなく `get_database_url()` を正とする。
- スキーマ変更時はモデルを編集してから自動生成し、差分を確認する:

```bash
uv run alembic revision --autogenerate -m "変更内容"
uv run alembic upgrade head
```

- 作り直したいとき: `docker compose down -v && docker compose up -d` でボリュームごと初期化し、`./scripts/migrate.sh` を再実行する。
