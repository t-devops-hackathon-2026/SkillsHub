# Step1 実装プラン

Step1 を完成させるための作業を、上から順番に潰せば動くものができあがる順序で並べた。各 issue は1人がオーナーになれる粒度に揃え、目的（なぜやるか）・やること（何をやるか）・完了条件（どう確認するか）を明記している。

- 仕様書: [docs/designs/step1/step1.md](docs/designs/step1/step1.md)
- 総括: [docs/designs/overview/overview.md](docs/designs/overview/overview.md)
- デモ: [demos/step1/demo.html](demos/step1/demo.html)
- Projects: https://github.com/orgs/t-devops-hackathon-2026/projects/3/views/1

## 凡例

- 🧪 スパイク / 📋 提出準備 / 🔧 インフラ / 💾 DB / 🐍 コード基盤 / 🐙 GitHub / 🤖 エージェント / 🖥️ 画面 / 🚀 デプロイ / 📝 仕上げ

---

## 📋 #1 Protopedia 提出方法の事前確認

**目的**: ハッカソンの提出先である [Protopedia](https://protopedia.net/) が求めるフォーマット・必須項目を最初に把握して、開発と並行で素材を貯められるようにする。後で「動画が必要だった」「特定の画像サイズ指定があった」と気づくのを防ぐ。

**やること**:
- Protopedia の提出ページ／ハッカソン用提出ガイドを読み、提出フォームの必須項目を洗い出す
- 必要そうな素材をリストアップ（タイトル／概要／デモURL／デモ動画／スクリーンショット／使用技術／チームメンバー など）
- 動画の長さ・サイズ・形式の指定があるか確認
- 公開／非公開設定や、後から編集できるかを確認
- 結果を本issueにコメントで残し、必要なら README に「提出物チェックリスト」セクションを作る

**完了条件**:
- [ ] 提出に必要な項目の一覧がissueコメントで共有されている
- [ ] 動画・スクショ等、開発と並行で準備すべき素材が明確になっている

---

## 🧪 #2 ADKの動作確認スパイク

**ADKとは何か**: Google Agent Development Kit。今回エージェント（Collector / Analyzer / Searcher など）を作る公式フレームワーク（Python製）。

**何を確かめるか・なぜやるか**:
- ADKには「Agentに構造化出力（`output_schema`）を強制すると tools が同時に使えない」というクセがあり、設計の前提になっている。実機で本当にそうかを確認しないと後の設計が崩れる
- `SequentialAgent` で前段の結果（`output_key`）を後段に渡せるかも確認
- Gemini モデル指定（Flash 既定／重い処理だけ Pro）の切り替え方も確認

**やること**:
- ADK をローカルにインストールし、最小サンプルを動かす
- `output_schema` + tools 併用したらどうなるか試す（エラー内容を記録）
- `SequentialAgent` で 2段つないで前段結果を後段が読める例を1つ作る

**完了条件**:
- [ ] 結論を本 issue にコメントで残す（「設計通りでOK」or「ここを変える必要あり」）

---

## 🔧 #3 GCP プロジェクト作成と2名招待

**目的**: 全員が触れる共通の staging 環境を立ち上げる。以降の全 GCP リソースはここに置く。

**やること**:
- GCP プロジェクト作成（プロジェクト ID 命名・課金紐付け・リージョン `asia-northeast1`）
- gcloud CLI のセットアップ手順を README に記載
- 必要 API 一括有効化（Cloud Run / Cloud SQL Admin / Vertex AI / Secret Manager / Cloud Build / Cloud Scheduler / Artifact Registry / Cloud Logging / IAM Credentials）
- 他2名の Google アカウントを `Editor` ロールで招待

**完了条件**:
- [ ] 他2名が `gcloud projects describe <ID>` でプロジェクト情報を取得できる

---

## 🔧 #4 サービスアカウント・IAM・ネットワーク・Secret 枠

**目的**: のちのち Cloud Run（Streamlit／司書バッチ）が Cloud SQL に繋ぎ、Secret Manager からパスワードや鍵を安全に読み出せるようにしておく。これをやらないとデプロイ後に動かない。

**やること**:
- サービスアカウント2つ作成
  - `streamlit-sa`（Streamlit サービス用）
  - `librarian-sa`（司書バッチ用）
- 各 SA に最小権限のロールを付与
  - Cloud SQL Client（DB に繋ぐため）
  - Secret Manager Secret Accessor（鍵を読むため）
  - Vertex AI User（Gemini / Embeddings 呼び出しのため）
  - Artifact Registry Reader（自分のコンテナを取得するため）
- Serverless VPC Access コネクタ作成（Cloud Run が Cloud SQL の private IP に繋ぐ経路）
- Secret Manager に空の Secret 枠を3つ用意（中身は #5 以降で投入）
  - `db-password`
  - `github-app-private-key`
  - `github-app-id`

**完了条件**:
- [ ] ローカルから `gcloud secrets list` で3つ見える状態

---

## 💾 #5 Cloud SQL 構築＋pgvector＋Artifact Registry

**目的**: Skill 本体・埋め込み・提案を保存するデータ基盤を立ち上げる。Artifact Registry は後のコンテナ置き場として一緒に作る。

**やること**:
- Cloud SQL for PostgreSQL 作成（最小サイズ・private IP）
- DB（`skillhub`）とアプリ用ユーザー作成、パスワードを Secret Manager の `db-password` に投入
- `CREATE EXTENSION vector;` で pgvector 有効化
- Cloud SQL Connector でローカルから接続確認（`psql` で `\dx` に `vector` が見える）
- Artifact Registry（Docker形式）リポジトリ作成

**完了条件**:
- [ ] ローカルから DB 接続できて、空の DB に `vector` 拡張が入っている

---

## 💾 #6 DB スキーマ定義＋マイグレーション＋シード

**目的**: Step1 で使う 5テーブルを定義して DB に反映。以降のすべての実装がこれを前提にする。

**やること**:
- `db/ddl.sql` に5テーブル定義
  - `repository` / `skill`（quality 系除く＋`content_hash`）／ `skill_embedding`（vector(768)）／ `suggestion` / `suggestion_target`
- インデックス追加（`update_status` / `updated_at` / pgvector 用 ivfflat or hnsw）
- **マイグレーション運用方針を決める（Alembic か 素の SQL）** — ローカル DB（#7 / `compose.yaml`）は pgvector 拡張のみの空 DB で、`docker-entrypoint-initdb.d` の init SQL は初回起動時に1回走るだけ。継続的なテーブル作成・変更を管理する仕組みは無いため、テーブルを作る前にこの方針決定が前提になる
- 初期シード投入スクリプト（手動 Skill 2件＋空の Repository 1件）

**完了条件**:
- [ ] staging の DB にスキーマ＋シードが入る

---

## 🐍 #7 Python プロジェクト初期化＋`shared/` 骨格

**目的**: 全実装の土台。画面・バッチ・テストがここに乗る。

**やること**:
- ディレクトリ構成（`app/` `shared/` `batch/` `db/` `tests/`）
- `uv` + `pyproject.toml`、依存追加（streamlit / google-adk / google-cloud-aiplatform / google-cloud-secret-manager / sqlalchemy / psycopg[binary] / pgvector / pyjwt / httpx / pydantic）
- 開発依存（ruff / mypy / pytest）
- `.env.example` `.gitignore` `pre-commit`
- `shared/config.py`（Secret Manager 読み込み）
- `shared/db.py`（SQLAlchemy エンジン・セッション）
- `shared/schemas.py`（Pydantic モデル：Skill / Suggestion / SearchResult）
- `shared/services.py`（関数の枠だけ）

**完了条件**:
- [ ] `uv run python -c "from shared.db import get_session; ..."` が DB 接続成功

---

## 🐙 #8 GitHub App＋`github_tools.py` 実装

**目的**: 司書が GitHub の登録リポジトリから SKILL.md を取得できるようにする。

**やること**:
- GitHub App 作成（権限: Contents Read-only / Metadata Read-only）
- 秘密鍵を Secret Manager `github-app-private-key` に登録、App ID を `github-app-id` に登録
- ハッカソン Org にインストール、`installation_id` を取得
- `shared/tools/github_tools.py` 実装:
  - JWT 生成 → Installation Access Token 取得
  - リポジトリ列挙（`GET /installation/repositories`）
  - Trees API（recursive=1）で SKILL.md 検出
  - Contents API で本文取得
  - Commits API で最終コミット・作者取得
  - ETag / Conditional Request＋指数バックオフ
  - `content_hash`（SHA-256）計算

**完了条件**:
- [ ] `python -m shared.tools.github_tools owner/repo` で SKILL 一覧と本文が print される

---

## 🤖 #9 司書バッチ① Collector＋Analyzer＋鮮度判定

**目的**: 取ってきた SKILL.md を構造化解析し、鮮度を判定する。

**やること**:
- `CollectorAgent`: github_tools を使い `content_hash` 比較で変更分のみ通す
- `AnalyzerAgent`: `output_schema` で name / description / tags / usage を構造化
- 鮮度判定（90日／180日しきい値・環境変数化）
- `needs_update` 検知時の `update` 提案（diff 下書き）生成

**完了条件**:
- [ ] ローカルで1リポジトリ → Skill 構造化結果が print。鮮度が期待通り

---

## 🤖 #10 司書バッチ② 埋め込み生成＋Deduper（merge 提案）

**目的**: Skill 本文をベクトル化し、重複候補（merge 提案）を自動生成する。

**やること**:
- Vertex AI `text-multilingual-embedding-002` で本文を vector(768) 化
- `skill_embedding` テーブルに upsert
- `DeduperAgent`: pgvector cosine 近傍検索 → 類似度 0.88 以上で `merge` 提案生成
- 自己・同一 `source_path` 除外、しきい値は環境変数化

**完了条件**:
- [ ] 類似ダミーを投入 → `suggestion` に `merge` が1件入る

---

## 🤖 #11 司書オーケストレーション＋Cloud Run Jobs エントリ

**目的**: ここまでの司書部品を1本のバッチにまとめ、定期実行できる形にする。

**やること**:
- `shared/agents/librarian.py`（SequentialAgent: Collector → Analyzer → Embed → Dedup）
- リポジトリ単位の独立実行（1件失敗で全体を止めない）
- 構造化ログ（収集数 / 失敗数）
- `batch/run_collect.py`（Cloud Run Jobs エントリ・`--repo-id` で即時収集も可）

**完了条件**:
- [ ] ローカル `python -m batch.run_collect` で1ループ完走・DB 反映

---

## 🤖 #12 オンライン: Searcher＋Composer＋サービス層

**目的**: ユーザーが自然文で検索 → 候補と合成提案を受け取れる動作をバックエンドで成立させる。

**やること**:
- `SearcherAgent`: クエリベクトル化 → 近傍検索 → 確信度・推薦理由を構造化出力
- `ComposerAgent`: `output_schema` で合成提案
- `shared.services.search_skills(query)`: 候補2件以上で Composer 呼ぶ分岐
- `shared.services.collect_repo(repo_id)`: 即時収集
- `shared.services.get_summary()`: サマリ集計（Skills 数 / 重複 / 要更新 / 陳腐化注意）

**完了条件**:
- [ ] `search_skills("議事録 要約")` が候補＋合成提案を返す

---

## 🖥️ #13 Streamlit 骨格＋ダッシュボード画面

**目的**: アプリの土台と最初の見える画面。ここから利用者導線が始まる。

**やること**:
- `app/main.py`（エントリ・ナビ・`st.session_state` 設計）
- `pages/dashboard.py`（サマリカード4枚＋絞り込み・ソート＋Skill カードグリッド）

**完了条件**:
- [ ] シードデータでカードが並び、絞り込み・ソートが動く

---

## 🖥️ #14 自然言語検索画面

**目的**: 「やりたいこと」を文章で投げて AI エージェントが候補を返す体験を作る。

**やること**:
- `pages/search.py`（チャット UI・途中表示・候補3件・確信度・推薦理由・合成提案）
- 候補カード → 詳細画面に遷移

**完了条件**:
- [ ] 「議事録を要約したい」で候補＋合成提案が表示される

---

## 🖥️ #15 Skill 詳細＋提案レビュー画面

**目的**: Skill の詳細閲覧と、司書が出した提案の採用／却下を画面で完結させる。

**やること**:
- `pages/detail.py`（ヘッダ・使い方・open 提案の承認/却下・GitHub issue 誘導）
- `pages/suggestions.py`（open 一覧＋diff＋採用/却下、update 採用時に鮮度を `new` に戻す挙動）

**完了条件**:
- [ ] カード → 詳細 → 提案承認の一連が動く

---

## 🖥️ #16 リポジトリ登録画面

**目的**: 司書の収集対象（Org / repo）を画面から管理できるようにする。

**やること**:
- `pages/repos.py`（登録済み一覧＋新規登録フォーム＋「今すぐ収集」ボタン）

**完了条件**:
- [ ] 新規登録 → 今すぐ収集 → ダッシュボードに反映

---

## 🚀 #17 Dockerfile＋Cloud Build＋Cloud Run デプロイ

**目的**: ローカルで動いているものを本番 URL で他人が触れる状態にする。

**やること**:
- `Dockerfile.app`（Streamlit）／`Dockerfile.batch`（司書 Job）
- Cloud Build トリガー（push → ビルド → Artifact Registry）
- Cloud Run service デプロイ（`streamlit-sa` で実行）
- Cloud Run Jobs デプロイ（`librarian-sa` で実行）
- VPC コネクタ経由で Cloud SQL に到達

**完了条件**:
- [ ] 本番 URL にブラウザでアクセスしてダッシュボードが表示

---

## 🚀 #18 Cloud Scheduler 設定＋初回バッチ動作確認

**目的**: 司書を完全自律稼働にする（毎日勝手に走る）。

**やること**:
- Cloud Scheduler ジョブ作成（毎日 03:00 JST で Cloud Run Jobs をキック）
- 手動トリガーで1回実行 → Cloud Logging でログ確認
- 実 Org を1つ登録 → 1日分の収集が走る

**完了条件**:
- [ ] 手動実行 → DB に新規 Skill 保存 → ダッシュボードに反映

---

## 📝 #19 README 整備＋デモシナリオ＋シード Skill 準備

**目的**: 提出物として第三者が読んで分かる状態にする。デモも成立させる。

**やること**:
- README にローカル起動・staging 参加・デプロイ手順・環境変数一覧
- デモシナリオ（リポジトリ登録 → 収集 → 検索 → 提案承認 を5分で見せる）
- シード Skill の実リポジトリを1〜2個用意（議事録要約・タスク抽出など）
- #1 で確認した Protopedia 提出物の素材を最終確認

**完了条件**:
- [ ] 別の人が README だけ見て staging 環境にアクセスできる
- [ ] Protopedia 提出に必要な素材が揃っている

---

## 並行作業のコツ（3人前提）

### 全体マップ

```
Day 1-2  | #1 Protopedia調査       #2 ADKスパイク（誰か1人が代表）
         |
Day 1-3  | #3 GCP → #4 SA/IAM → #5 SQL+AR
         |
Day 3-5  | #6 DBスキーマ                              ← #5を待つ
         | #7 Python+shared骨格                       ← #6のスキーマ案だけあれば着手可
         |
Day 5-8  | #8 GitHub App+tools    #12 Searcher/サービス層    #13 ダッシュボード
         | （#7が終われば即着手）  （#7完了で着手）            （#7でPydantic確定すれば着手）
         |
Day 8-10 | #9 Collector+Analyzer  #14 検索画面               #15 詳細+提案
         |
Day10-12 | #10 Embed+Dedup        #16 repo登録画面
         | #11 オーケストレーション
         |
Day12-13 | #17 Dockerfile+Deploy  ← ここから全員集合
         | #18 Scheduler
         |
Day13-14 | #19 README+デモ準備    ← 全員で仕上げ
```

### 並行のコツ 7つ

1. **#2（ADKスパイク）は最初に全員参加で30分読み合わせる**
   代表1人が実装スパイクするが、結論を全員が共有しておくと後で「これ ADK だとできない」みたいな手戻りを防げる。

2. **#6（DBスキーマ）は DDL を書き上げる前に ER 図を1枚 Slack や Issue に貼って合意する**
   スキーマが揺れると `shared/schemas.py`（#7）・画面のカラム参照（#13-16）・サービス層（#12）が全部影響を受ける。DDL 確定が遅れるとボトルネックになる。「カラム名と型だけ先に決める → DDL は後でも書ける」と進む。

3. **#7（shared 骨格）は最初の数時間で Pydantic だけ先に commit する**
   `Skill` `Suggestion` `SearchResult` の Pydantic クラスさえあれば、フロントエンド（#13-16）はダミーデータで先行できる。逆に Pydantic が揺れるとフロントエンド全員が手戻りなので、早めに固めて変更時は Slack 通知。

4. **フロントエンドはダミーデータで先行し、バックエンド完成後に差し替え**
   `shared/services.py` の関数を「固定値を返すモック」で先に実装 → 画面はそれを呼ぶ → #12 完成時に中身だけ差し替え。これで画面チームは #12 を待たずに4画面分作り切れる。

5. **#8（GitHub App）と #9（Collector / Analyzer）の間に「サンプル SKILL.md をローカルファイルに置く」モードを挟む**
   GitHub App 作成は管理画面操作で詰まりがちなので、その間に Analyzer は「ローカルファイルから読む」モードで開発を始められる。後で Collector と繋ぐだけ。

6. **#17（デプロイ）を中盤に1回試す（"スモークデプロイ"）**
   最後に一気にデプロイすると環境差分でハマる。Day 8 あたりで「Hello World 相当の Streamlit」を一度 Cloud Run に出してみる。VPC コネクタ・Cloud SQL 接続・Secret 読み込みがそこで通っていれば、最終デプロイは安心。

7. **PR は小さくレビュアー全員参加で回す**
   3人ならお互いの作業がすぐ影響するので、PR は1機能ずつ小さく出して Slack で即レビュー。30分以内レビュー目標。マージ後は main を各自 pull して同期。

### 詰まりやすいポイント早見表

| 詰まりどころ | 早めにやる回避策 |
|---|---|
| GCP API 有効化漏れ | #3 でリストを全部チェック、足りなかったら README に追記 |
| Cloud SQL private IP 接続 | #5 の段階で Cloud SQL Connector のローカル接続を必ず試す |
| GitHub App の `installation_id` 取得 | #8 で取れた値をすぐ Secret / 環境変数化、再取得手順を README へ |
| ADK `output_schema` 制約 | #2 で先に潰す。ハマったら設計を見直す勇気を持つ |
| Streamlit `session_state` の挙動 | #13 で骨格を作ったらすぐ #14-16 で reuse できる形にしておく |
| pgvector しきい値 0.88 が高すぎ／低すぎ | #10 で実データを入れたら早めに調整、しきい値は環境変数化 |
| Protopedia 提出フォーマット | #1 で先に把握、動画やスクショは開発と並行で撮りためる |
