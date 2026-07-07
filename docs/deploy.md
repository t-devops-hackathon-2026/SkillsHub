# デプロイ（Cloud Run）

構成の正は [overview.md「インフラ構成（デプロイ）」](designs/overview/overview.md)。イメージは1本（[`Dockerfile`](../Dockerfile)、[`cloudbuild.yaml`](../cloudbuild.yaml) でビルド）を Cloud Run サービス（Streamlit）・司書 Job・マイグレーション Job で共用し、起動コマンドだけを変える。

## 前提（一度だけ）

Cloud SQL（private IP）・Artifact Registry リポジトリ `skillshub`・サービスアカウント（`streamlit-sa` / `librarian-sa`）・VPC コネクタ `skillshub-conn` は #10 で作成済みであること。Secret Manager には次の4つを登録しておく: `DATABASE_URL`、`app-password`、`github-app-id`、`github-app-private-key`。

```bash
PROJECT_ID=$(gcloud config get-value project)
REGION=asia-northeast1
IMAGE=$REGION-docker.pkg.dev/$PROJECT_ID/skillshub/app:latest

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  aiplatform.googleapis.com secretmanager.googleapis.com

for SA in streamlit-sa librarian-sa; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done
```

## 1. イメージのビルド

```bash
gcloud builds submit --config cloudbuild.yaml
```

## 2. マイグレーション Job

```bash
gcloud run jobs create migrate \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="librarian-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --vpc-connector=skillshub-conn \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest \
  --set-env-vars=SEED_DEMO_SKILLS=0

gcloud run jobs execute migrate --region="$REGION" --wait
```

## 3. Streamlit サービス

```bash
gcloud run deploy skillshub \
  --image="$IMAGE" \
  --region="$REGION" \
  --command=./scripts/serve.sh \
  --service-account="streamlit-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --vpc-connector=skillshub-conn \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest,APP_PASSWORD=app-password:latest,GITHUB_APP_ID=github-app-id:latest,GITHUB_APP_PRIVATE_KEY=github-app-private-key:latest \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION=global,SEED_DEMO_SKILLS=0 \
  --allow-unauthenticated \
  --min-instances=0 --max-instances=1 \
  --timeout=3600 \
  --memory=1Gi
```

## 4. 司書 Job（収集バッチ）

```bash
gcloud run jobs create librarian \
  --image="$IMAGE" \
  --region="$REGION" \
  --command=python \
  --args=-m,skillshub.batch.run_collect \
  --service-account="librarian-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --vpc-connector=skillshub-conn \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest,GITHUB_APP_ID=github-app-id:latest,GITHUB_APP_PRIVATE_KEY=github-app-private-key:latest \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION=global,SEED_DEMO_SKILLS=0 \
  --task-timeout=30m

gcloud run jobs execute librarian --region="$REGION" --wait
```

## 5. 動作確認

サービス URL をブラウザで開き、パスワードゲート → ダッシュボード表示を確認する。画面から収集元を追加して「今すぐ同期」を実行するか、`librarian` Job を手動 execute して、収集された Skill がダッシュボードに並べば疎通完了。

## イメージ更新時

スキーマ変更を含む更新では migrate を先に更新・実行してからサービスを切り替えること:

```bash
gcloud run jobs update migrate --image="$IMAGE" --region="$REGION"
gcloud run jobs execute migrate --region="$REGION" --wait
gcloud run services update skillshub --image="$IMAGE" --region="$REGION"
gcloud run jobs update librarian --image="$IMAGE" --region="$REGION"
```

---

## インフラ構成（staging）

実際のプロジェクト ID はチーム共有情報を参照し、`<PROJECT_ID>` を置き換えて読むこと。

- プロジェクト: `<PROJECT_ID>`（`asia-northeast1`）
- サービスアカウント
  - `streamlit-sa@<PROJECT_ID>.iam.gserviceaccount.com` — Streamlit / Cloud Run service
  - `librarian-sa@<PROJECT_ID>.iam.gserviceaccount.com` — 司書バッチ / Cloud Run Jobs
- Secret Manager: `db-password`（実値投入済み）、`github-app-private-key` / `github-app-id`
- Artifact Registry: `asia-northeast1-docker.pkg.dev/<PROJECT_ID>/skillshub`
- VPC コネクタ: `skillshub-conn`（READY）
- Cloud SQL: インスタンス `skillhub-pg`（PostgreSQL 16 / private IP のみ / pgvector 有効）

### staging DB への一時接続手順

```bash
# 一時的に public IP を開放
gcloud sql instances patch skillhub-pg --assign-ip --quiet

# Auth Proxy を起動（別ターミナル）
cloud-sql-proxy <PROJECT_ID>:asia-northeast1:skillhub-pg --port 5432

# psql で接続
PGPASSWORD="$(gcloud secrets versions access latest --secret=db-password)" \
  /usr/local/opt/libpq/bin/psql "host=127.0.0.1 port=5432 user=app dbname=skillhub"

# 作業が終わったら必ず private のみに戻す
gcloud sql instances patch skillhub-pg --no-assign-ip --quiet
```
