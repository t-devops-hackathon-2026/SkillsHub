# SkillsHub（仮称） — 社内Skillsダッシュボード

## ローカル開発

ローカル開発では、ローカルに立てた DB（pgvector 入り PostgreSQL）を使う。staging などのリモート DB には接続しない（共有環境の汚染や認証情報のローカル流出を避けるため）。デプロイ環境のみが Secret Manager 経由で Cloud SQL を参照する。

### セットアップ

```bash
# 1. 環境変数ファイルを用意（既定で下記ローカル DB を指す）
cp .env.example .env

# 2. ローカル DB（pgvector）を起動
docker compose up -d

# 3. 依存をインストール
uv sync

# 4. スキーマ適用＋初期シード投入（マイグレーション→seed をまとめて実行）
./scripts/migrate.sh
```

> `scripts/migrate.sh` は `alembic upgrade head` と `python -m skillshub.db.seed`（手動 Skill 2件＋空 Repository 1件・冪等）を順に流すだけのラッパ。個別に流したい場合は `uv run alembic upgrade head` / `uv run python -m skillshub.db.seed` を直接実行してもよい。staging では同じ `migrate.sh` を Cloud Run Job のコマンドとして使う。手動 Skill 2件は架空のデモデータのため、本番・staging では環境変数 `SEED_DEMO_SKILLS=0` を設定して投入をスキップする（「初期状態に戻す」ボタン経由の再シードにも同じガードが効く）。

### 接続確認

```bash
uv run python -c "from skillshub.shared.db import get_session; s=next(get_session()); s.execute(__import__('sqlalchemy').text('select 1')); print('DB OK')"
```

`DB OK` が出力されればローカル DB への接続成功。

### Gemini の認証

LLM・埋め込みの呼び出しは google-genai SDK 経由で、認証は環境変数で切り替わる。ローカルでは `.env` に `GOOGLE_API_KEY`（Gemini API キー）を設定するのが手軽。デプロイ環境では API キーを使わず、`GOOGLE_GENAI_USE_VERTEXAI=TRUE`・`GOOGLE_CLOUD_PROJECT`・`GOOGLE_CLOUD_LOCATION` を設定してサービスアカウントの ADC で Vertex AI を呼ぶ。

### 公開デプロイ時のアクセス制限

Cloud Run に `--allow-unauthenticated` でデプロイすると URL を知っていれば誰でも操作できるため、アプリ側に簡易パスワードゲートを備えている。環境変数 `APP_PASSWORD` を設定するとアプリ表示前にパスワード入力を求める（未設定のローカル開発ではゲートは出ない）。デプロイ時は Secret Manager に登録し、`--set-secrets=APP_PASSWORD=app-password:latest` のように環境変数として渡す。

### スキーマ／マイグレーション

- スキーマの正は ORM モデル [`skillshub/shared/models.py`](skillshub/shared/models.py)（カラム定義の出典は [`docs/designs/step1/er.md`](docs/designs/step1/er.md)）。
- マイグレーションは **Alembic**（`skillshub/db/migrations/`）。接続先は `alembic.ini` ではなく `get_database_url()` を正とする（ローカルは `.env`、デプロイ環境は Secret Manager）。
- 初回起動時、`skillshub/db/init/01_extension.sql` が pgvector 拡張を有効化する。テーブル作成は `alembic upgrade head` が担う（初回マイグレーションも `CREATE EXTENSION IF NOT EXISTS vector` を冪等に実行するため、staging など init を通らない環境でも単体で完結する）。
- スキーマ変更時はモデルを編集してから自動生成し、差分（特に pgvector の hnsw インデックス等）を確認する:

```bash
uv run alembic revision --autogenerate -m "変更内容"
uv run alembic upgrade head
```

- 作り直したいとき: `docker compose down -v && docker compose up -d` でボリュームごと初期化し、`./scripts/migrate.sh` を再実行する。
- staging（Cloud SQL / private IP）への適用は、同じ `migrate.sh` を `Dockerfile` でイメージ化し、VPC コネクタ付きの Cloud Run Job として流す（public IP 開放は不要）。

---

## デプロイ（Cloud Run）

構成の正は [overview.md「インフラ構成（デプロイ）」](docs/designs/overview/overview.md)。イメージは1本（[`Dockerfile`](Dockerfile)、[`cloudbuild.yaml`](cloudbuild.yaml) でビルド）を Cloud Run サービス（Streamlit）・司書 Job・マイグレーション Job で共用し、起動コマンドだけを変える。以下を上から順に流せば再現できる。

### 前提（一度だけ）

Cloud SQL（private IP）・Artifact Registry リポジトリ `skillhub`・サービスアカウント（`streamlit-sa` / `librarian-sa`）・VPC コネクタ `skillshub-connector` は #10 で作成済みであること。Secret Manager には次の4つを登録しておく: `DATABASE_URL`（private IP 向け接続文字列）、`app-password`（画面のパスワードゲート用）、`github-app-id`、`github-app-private-key`。

```bash
PROJECT_ID=$(gcloud config get-value project)
REGION=asia-northeast1
IMAGE=$REGION-docker.pkg.dev/$PROJECT_ID/skillhub/app:latest

# API 有効化
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  aiplatform.googleapis.com secretmanager.googleapis.com

# 実行 SA に Vertex AI 呼び出しと Secret 参照の権限を付与
for SA in streamlit-sa librarian-sa; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done
```

### 1. イメージのビルド

```bash
gcloud builds submit --config cloudbuild.yaml
```

main への push で自動ビルドしたい場合は、Cloud Build の GitHub トリガー（構成ファイル: `cloudbuild.yaml`）を作成する。タグは `latest` のみの運用。

### 2. マイグレーション Job（スキーマ適用＋seed）

```bash
gcloud run jobs create migrate \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="librarian-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --vpc-connector=skillshub-connector \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest \
  --set-env-vars=SEED_DEMO_SKILLS=0

gcloud run jobs execute migrate --region="$REGION" --wait
```

`SEED_DEMO_SKILLS=0` は架空のデモ Skill（alice / bob）を本番に入れないためのガード。

### 3. Streamlit サービス

```bash
gcloud run deploy skillhub \
  --image="$IMAGE" \
  --region="$REGION" \
  --command=./scripts/serve.sh \
  --service-account="streamlit-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --vpc-connector=skillshub-connector \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest,APP_PASSWORD=app-password:latest,GITHUB_APP_ID=github-app-id:latest,GITHUB_APP_PRIVATE_KEY=github-app-private-key:latest \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION="$REGION",SEED_DEMO_SKILLS=0 \
  --allow-unauthenticated \
  --min-instances=0 --max-instances=1 \
  --memory=1Gi
```

`--allow-unauthenticated` で URL は公開になるが、アプリ側のパスワードゲート（`APP_PASSWORD`）で操作を保護する。`--max-instances=1` は Streamlit のセッションがインスタンスローカルなことへの対策（スケールアウトさせない）。サービス側にも `SEED_DEMO_SKILLS=0` を渡すのは、「初期状態に戻す」ボタンが再シードを呼ぶため。

### 4. 司書 Job（収集バッチ）

```bash
gcloud run jobs create librarian \
  --image="$IMAGE" \
  --region="$REGION" \
  --command=python \
  --args=-m,skillshub.batch.run_collect \
  --service-account="librarian-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --vpc-connector=skillshub-connector \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest,GITHUB_APP_ID=github-app-id:latest,GITHUB_APP_PRIVATE_KEY=github-app-private-key:latest \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION="$REGION" \
  --task-timeout=30m

gcloud run jobs execute librarian --region="$REGION" --wait
```

日次の自動実行（Cloud Scheduler からのキック）は #23 で設定する。

### 5. 動作確認

サービス URL をブラウザで開き、パスワードゲート → ダッシュボード表示を確認する。画面から収集元を追加して「今すぐ同期」を実行するか、`librarian` Job を手動 execute して、収集された Skill がダッシュボードに並べば疎通完了。

### イメージ更新時の注意

Cloud Run は `:latest` タグをデプロイ時点の digest に固定するため、新しいイメージをビルドしただけでは反映されない。ビルド後に次を流して新リビジョン／新実行に切り替える:

```bash
gcloud run services update skillhub --image="$IMAGE" --region="$REGION"
gcloud run jobs update librarian --image="$IMAGE" --region="$REGION"
gcloud run jobs update migrate --image="$IMAGE" --region="$REGION"
```

---

## 前提知識：Skillsとは何か

本プロダクトが対象とするSkillsとは、`SKILL.md` のような統一規格で記述され、複数のAIエージェントから呼び出して利用できる再利用可能な機能単位を指す。一つのSkillは、何をするものかを説明する記述（description）、どんなときに使うかを示すトリガー条件、そして実際の処理を担うスクリプトやロジックから構成される。AIエージェントは会話の文脈に応じて適切なSkillを自動的に選び、呼び出して使う。規格やしくみの詳細はAnthropicの公式資料を参照してください。

（[Agent Skills 概要（Claude Docs）](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)、[Introducing Agent Skills（Anthropic）](https://www.anthropic.com/news/skills)、[Equipping agents for the real world with Agent Skills（Anthropic Engineering）](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)）。

統一規格であることがポイントで、特定のエージェントに縛られず、社内のさまざまなAIエージェントから同じSkillsを共有して利用できる。たとえば議事録を要約してタスクを抽出するSkill、特定の社内APIを叩くSkill、コードレビュー観点をまとめたSkillなどが考えられる。誰もが作って共有できる一方で、数が増えるほど管理が難しくなる性質を持つ。

なお本ドキュメントでは、こうした機能単位を一貫してSkillsと呼ぶ。

---

## プロダクト概要

統一規格で書かれた社内のSkillsを、AIエージェントが横断的に収集して可視化し、品質と鮮度を継続的に高め続けるダッシュボードである。

ダッシュボードはあくまで表示の器であり、価値の源泉はAIエージェントが担う。エージェントが散在するSkillsを自動で集めて棚卸しし、重複や陳腐化を分析して具体的な改善案を提示する。利用者はこのダッシュボードを通じて、必要なSkillsを探して使い、重複なく新しいSkillsを作れるようになる。

---

## 解決したい課題

### 課題

社内で作られたSkillsが各所に散在している。Skills専用のリポジトリを用意しているチームもあれば、別チームは自分たちのプロダクトのリポジトリ内にSkills用のディレクトリを切って置いている。個人がローカルや個人リポジトリに置いたまま共有していないケースもあり、SlackやNotionに貼られただけで正式な置き場を持たないものもある。結果として、社内のSkillsを一覧できる場所がどこにも存在しない。

この散らばりから、3つの問題が生じている。

#### 1. 把握できず繰り返し作られる

どこに何があるか把握できないため、すでに存在するSkillsと同じようなものが繰り返し作られる。

#### 2. 見つからず使い方も分からない

探しても見つからず、見つかっても使い方が分からないため活用されない。

#### 3. 放置されて動かなくなる

一度作られたSkillsが放置され、参照しているAPIや依存ツールの変更によって動かなくなっていく。

### 背景

AIエージェントの業務活用が広がり、`SKILL.md` のような統一規格でSkillsを記述・共有できるようになった結果、社内で作られるSkillsの数が急増した。一方で、それらを一元的に管理し、発見し、評価する仕組みが追いついていない。各チームや個人がそれぞれの流儀で置き場所を決めているため、全社的なカタログが存在せず、作る側も使う側も今あるものを知る手段を持たない。

### 課題の大きさ

この課題は、観点ごとに別々の損失を生んでいる。

#### 使われないことによる損失

せっかく作ったSkillsが発見されなければ、作成にかけた工数がそのまま無駄になる。良いSkillsほど多くの人に使われて初めて投資が回収されるが、見つからなければ作者一人の手元で眠ったままになる。これはAI活用への投資対効果を直接押し下げる。

#### 再発明による損失

既存のSkillsの存在を知らずに同じものを作ると、同じ工数が何度も二重三重に消費される。作る側の時間が奪われるだけでなく、似て非なるSkillsが乱立して全体の見通しがさらに悪くなり、散らばりを加速させる悪循環に陥る。

#### 使い方が分からないことによる損失

Skillsを見つけても、説明が不十分だと正しく使えず、試すこと自体を諦めてしまう。あるいは誤った使い方で期待外れの結果を得て、Skills全体への信頼が下がる。陳腐化したSkillsが誤動作すれば、この不信はさらに強まる。

これらは個別の不便にとどまらず、社内でAI活用が浸透していく速度そのものを左右する。Skillsの発見性と鮮度は、社内AI活用の基盤に位置する課題である。

### 発生頻度

Skillsを新しく作るたび、既存のSkillsを探すたびに発生する。日常的かつ継続的な課題であり、Skillsの総数が増えるほど深刻化する。特に新メンバーのオンボーディング時や、新しいユースケースに取り組むたびに、これに使えるSkillsはあるのかという問いが繰り返される。

---

## 対象となるユーザー

### Skillsを使う開発者

新しいタスクに取り組むとき、AIエージェントに任せたいが、社内にどんなSkillsがあるか分からない。検索しても見つからず、結局自分で一から作るか、エージェントの素の能力だけで進めてしまう。運良く見つけたSkillsがあっても、説明が不十分で使い方が分からず、試すのを諦めることがある。

### Skillsを作る作者

便利なSkillsを作ったものの、社内のどこに置けば見つけてもらえるか分からず、利用が広がらない。自分が作ろうとしているものがすでに存在するのか確認する術がなく、重複作成のリスクを常に抱えている。一度公開したSkillsが今も正しく動くのか、どこを改善すべきかというフィードバックも得られない。

---

## 実用性・体験価値の魅力

利用者にとっての中心的な価値は、社内Skillsの場所が可視化されて探しやすくなる点である。

Skillsを使う開発者は、自然言語でこういうことがしたいと問いかけるだけで、最適なSkillsと使い方の例が即座に提示される。探すコストと理解するコストが大きく下がる。

また、Skillsを作る作者は、作成前に重複を確認でき公開後はエージェントから具体的な改善案を受け取れる。改善案はそのまま取り込めるdiff形式の下書きとして提示されるため、作って終わりではなく育て続けられる。

さらに、エージェントが鮮度を監視し続けるため、カタログ全体が常に今使える状態に保たれる。発見して終わりではなく、発見・利用・改善が一つの場で循環する点に体験価値がある。

---

## AIエージェントの役割

ダッシュボードは表示の器に過ぎず、価値の源泉はAIエージェントが担う。採用する能力は次の5つである。

### 横断収集・可視化

社内のリポジトリやディレクトリからSkillsを自動で収集し、内容を解析して一覧・分類・タグ付けして可視化する。散在していたものを一箇所に集約する起点となる。

### 重複・類似検出

Skills間の類似度を分析し、再発明を検知する。統合やマージの候補を作者に提案し、似たものが乱立する状態を解消する。

### 品質・鮮度スコアリング

説明文の曖昧さ、トリガー記述の精度、注釈の不足を評価する。あわせて参照APIや依存ツールの変更を検知し、要更新の状態を判定して可視化する。

### 自然言語探索・ドキュメント生成

こうしたいという問いに対して最適なSkillsを提示し、使い方や利用例を自動生成する。使い方が分からないという課題に直接応える。

### Skills合成・オーケストレーション提案

複数のSkillsを組み合わせた新しいワークフローをエージェントが提案する。単体では足りない要求に対し、既存のSkillsの組み合わせで応える。

これらにより、エージェントは社内Skillsの鮮度を上げ続ける司書として機能する。

---

## データモデル

主要なエンティティと関係を以下に定義する。まずはハッカソンの初期提出物としてここまで組みたい。

### Skill

| フィールド | 型 | 説明 |
| --- | --- | --- |
| id | string | Skillの一意ID |
| name | string | Skill名 |
| description | string | 説明（SKILL.md 由来） |
| source_repo | string | 取得元リポジトリまたはパス |
| author | string | 作者（Gitメタデータ由来） |
| tags | string[] | エージェントが付与した分類タグ |
| last_updated | datetime | 最終更新日時（鮮度指標の元） |
| update_status | enum | fresh / stale / needs_update |
| quality_score | number | エージェント算出の品質スコア |
| created_at | datetime | 初回登録日時 |

### Suggestion

エージェントが生成する提案を表す。

| フィールド | 型 | 説明 |
| --- | --- | --- |
| id | string | 提案ID |
| skill_id | string | 対象Skill（合成提案では複数を参照） |
| type | enum | merge / improve / compose / update |
| content | string | 提案内容（improveではdiff形式の下書きを含む） |
| status | enum | open / accepted / dismissed |
| created_at | datetime | 生成日時 |

関係としては、一つのSkillが複数のSuggestionを持つ。compose型のSuggestionは複数のSkillを参照する。投票やコメントといった独自の評価機能は持たず、要望や議論はソースリポジトリのGitHub issueで行い、需要はStep後半で導入するオプトインの利用回数（匿名・集計）で可視化する。

---

## 画面案

### ダッシュボード（一覧）

収集された全Skillsをカード形式で表示する。タグ、品質スコア、鮮度ステータス（fresh / stale / needs_update をバッジ表示）でフィルタとソートができる。上部にはエージェントからの提案サマリーとして、重複候補が何件、要更新が何件あるかを集約して示す。

### Skill詳細

選択したSkillの説明、自動生成された使い方と利用例、作者、最終更新日、品質スコアの内訳を表示する。エージェントの改善提案（diff下書き）もここで確認できる。要望や議論はソースリポジトリのGitHub issueへ誘導する。

### 自然言語検索

こういうことがしたいという入力に対して、エージェントが最適なSkillsを提示する。複数のSkillsを組み合わせた合成提案もここで返す。

### 提案レビュー

エージェントが生成した提案（重複統合、改善、合成、鮮度更新）を一覧化する。作者や管理者が採用するか却下するかを判断できる。

---

## ユーザーストーリー

### 使う開発者のエピソード

バックエンド開発者の田中は、毎週の定例後に議事録を整理して担当者ごとのタスクに振り分ける作業を手作業で続けていた。AIエージェントに任せたいと考えたが、社内に使えるSkillsがあるのか分からない。これまでは、こういうとき自分でプロンプトを試行錯誤するか、結局手作業に戻っていた。

田中はダッシュボードの自然言語検索に、議事録を要約して担当者別のタスクに分けたいと入力する。エージェントは議事録要約Skillとタスク抽出Skillを提示し、さらにこの二つを組み合わせた合成ワークフローを提案する。詳細画面には自動生成された利用例が載っているため、田中はそのまま自分の議事録で試し、期待どおりに動くことを確認する。翌週からは定例後の整理が数分で終わるようになる。

### 作る作者のエピソード

データ基盤チームの佐藤は、社内のデータカタログAPIを叩いてテーブル定義を取得するSkillを新しく作ろうとしていた。着手前にダッシュボードで類似Skillを検索すると、別のチームがほぼ同じ目的のSkillをすでに作っていたことが分かる。佐藤は一から作る代わりに、既存のSkillに自分が必要とする出力フォーマットの差分を加える方針に切り替え、重複作成を避けられた。

数週間後、参照していたデータカタログAPIの仕様が変わる。エージェントは依存先の変更を検知してそのSkillに要更新フラグを立て、修正案をdiff形式の下書きとして提示する。佐藤は提案レビュー画面で内容を確認し、ほぼそのまま採用する。手元での再現確認や原因調査に時間を取られることなく、Skillは再び今使える状態に戻る。提案やissueでの反応を通じて、自分のSkillが他チームでも使われていることを知り、改善のモチベーションにもつながる。

---

## 不確実性の高いこと

現時点で読みきれない要素を整理すると、次のとおりである。

- 統一規格Skillsの収集対象範囲をどこまで広げられるか。どのリポジトリや形式まで自動収集できるかによってカバレッジが大きく変わる。
- 品質スコアと鮮度判定の精度と納得感。スコアが現場の実感とずれると信頼を失う。
- 重複・類似検出の閾値設定。過検出と見逃しのバランスを取るための調整が必要になる。
- 自動生成した使い方ドキュメントの正確性。誤った例を出すと逆効果になる。
- 利用記録（オプトイン）の設定や提案採用が十分に回るか、つまりエンゲージメントが立ち上がるか。

---

## 今回は解決しない課題

今回のスコープから外すものは次のとおりである。

- Skillsの実行内容・入出力ログの取得や本格的な利用分析。利用回数はStep後半でオプトイン・匿名の最小収集にとどめ、需要の代替指標とする。
- アクセス制御や権限管理の厳密な実装。
- Skillsの自動デプロイや実行基盤そのものの提供。あくまで発見・評価・改善のレイヤーに集中する。
- ギャップ分析（検索ログや利用状況から、求められているのに存在しないSkillsを見つけて新規作成を促す機能）。有望だが将来拡張とする。

---

## チェック項目への対応

### AIエージェントが価値の中心になっているか

ダッシュボードは表示の器であり、収集、重複検出、品質と鮮度のスコアリング、自然言語探索とドキュメント生成、Skills合成提案という中核機能はすべてAIエージェントが担う。エージェントがなければ成立しない設計になっている。

### ユーザーが直観的に使える機能やデザインを有しているか

自然言語で問いかけるだけのSkills検索、バッジによる鮮度の一目把握、その場で取り込めるdiff形式の改善提案など、ログイン不要で専門知識がなくても直観的に操作できる導線を備える。

### プロダクトとしてのストーリー

一貫性については、社内Skillsの鮮度を上げ続けるという一つの目的に、収集から評価、探索、改善、合成までの全機能が貫かれている。妥当性については、Skillsの散在と重複、陳腐化という実在する課題に対し、収集と評価という直接的な手段で応えている。実行内容のログに頼らず、オプトインの利用回数と鮮度で需要・価値を見極める割り切りも、取得難度と価値のバランスを取った現実的な設計である。新規性については、単なるSkillsの置き場ではなく、エージェント自身がSkillsを評価し改善し合成して、鮮度を能動的に高め続ける点に新しさがある。

## インフラ構成（staging）

デプロイ（#22）時に指定する値の控え。権限の詳細は `gcloud` / GCP コンソールで確認できる。

- **プロジェクト**: `t-skillshub-staging`（`asia-northeast1`）
- **サービスアカウント**（Cloud Run の `--service-account` に指定）
  - `streamlit-sa@t-skillshub-staging.iam.gserviceaccount.com` — Streamlit / Cloud Run service
  - `librarian-sa@t-skillshub-staging.iam.gserviceaccount.com` — 司書バッチ / Cloud Run Jobs
  - いずれも最小権限4ロール付与済み（Cloud SQL Client / Secret Accessor / Vertex AI User / Artifact Registry Reader）
- **Secret Manager**
  - `db-password` — #10 で実値投入済み（Cloud SQL `app` ユーザーのパスワード）
  - `github-app-private-key` / `github-app-id` — 枠のみ（実値は #13 で投入）
- **Artifact Registry**（Docker）: `asia-northeast1-docker.pkg.dev/t-skillshub-staging/skillshub`
  - デプロイ #22 でビルドしたコンテナイメージの push 先
- **VPC コネクタ**: `skillshub-conn`（#10 で作成済み・READY）。Cloud Run → Cloud SQL の private IP 経路。
  - レンジ `10.8.0.0/28` / `e2-micro` / min 2・max 10
  - デプロイ #22 で Cloud Run に `--vpc-connector=skillshub-conn` を指定する

  ```bash
  # 参考: 作成時のコマンド（作成済みのため再実行は不要）
  gcloud compute networks vpc-access connectors create skillshub-conn \
    --region=asia-northeast1 --network=default --range=10.8.0.0/28
  ```
- **private services access**: `google-managed-services-default`（VPC ピアリング設定済み。Cloud SQL の private IP の前提）
- **Cloud SQL**（#10 で構築済み）
  - インスタンス: `skillhub-pg`（PostgreSQL 16 / `db-f1-micro` / ENTERPRISE エディション）
  - 接続名: `t-skillshub-staging:asia-northeast1:skillhub-pg`
  - DB: `skillhub` / アプリ用ユーザー: `app`（パスワードは Secret `db-password`）
  - **private IP のみ**（`10.28.0.3`）。pgvector（`vector` 拡張 0.8.1）有効化済み

### DB 接続方針（staging）

staging の Cloud SQL は **private IP のみ**（チーム合意済み・確定方針 / 通称「A案」）。経路を用途で分ける。

- **本番（Cloud Run）**: VPC コネクタ `skillshub-conn` 経由で private IP に接続（デプロイ #22）。
- **日常開発**: staging DB には繋がず、各自のローカル Docker（`postgres` + `pgvector`）で開発・テストする。
- **ステージング実データを確認したいとき（都度）**: 下記「一時接続手順」で、必要なときだけ public IP を一時開放して繋ぐ。**終わったら必ず private に戻す**。

> 補足: private のみのため、初期セットアップ（pgvector 有効化・スキーマ投入など）は一時的に public IP を開放して流すか、VPC 内ジョブ（Cloud Run Job / Cloud Build）で実行する。

#### staging DB への一時接続手順

```bash
# 0. 事前準備（初回のみ）
brew install cloud-sql-proxy libpq
gcloud auth application-default login        # ADC を用意
#    ※ 接続には IAM ロール roles/cloudsql.client が必要

# 1. 一時的に public IP を開放
gcloud sql instances patch skillhub-pg --assign-ip --quiet

# 2. Auth Proxy を起動（別ターミナルで）
cloud-sql-proxy t-skillshub-staging:asia-northeast1:skillhub-pg --port 5432

# 3. psql で接続（パスワードは Secret から取得）
PGPASSWORD="$(gcloud secrets versions access latest --secret=db-password)" \
  /usr/local/opt/libpq/bin/psql "host=127.0.0.1 port=5432 user=app dbname=skillhub"

# 4. 作業が終わったら必ず private のみに戻す
gcloud sql instances patch skillhub-pg --no-assign-ip --quiet
```

### 各サービスの役割（担当者以外向けのざっくり説明）

| サービス | ひとことで言うと | このプロジェクトでの役割 |
|---|---|---|
| **Cloud SQL（PostgreSQL）** | Google が運用してくれるDB | Skill 本体・提案・埋め込みベクトルを保存する中心のデータ置き場 |
| **pgvector** | PostgreSQL にベクトル検索を足す拡張 | Skill 本文をベクトル化して「意味が近いSkill」を検索・重複検出する |
| **private IP** | 社内ネット（VPC）からしか繋げない内線番号 | DB をインターネットに晒さないための非公開アドレス |
| **private services access（VPC ピアリング）** | 自分のVPCと Google 管理ネットを繋ぐ橋 | Cloud SQL に private IP を割り当てるための前提設定 |
| **Serverless VPC アクセスコネクタ** | Cloud Run から社内ネットへの出入口 | Cloud Run（公開）が DB の private IP に届くための経路 |
| **Secret Manager** | 鍵・パスワードの金庫 | DB パスワードや GitHub App の秘密鍵をコード外で安全に管理する |
| **Artifact Registry** | コンテナイメージの倉庫 | ビルドしたアプリ（Streamlit / 司書バッチ）のDockerイメージをためてCloud Run に配る |
| **Cloud SQL Auth Proxy** | IAM認証つきの安全なトンネル | 開発者PCから DB へ、IP許可リストなしで暗号化接続するための踏み台ツール |
