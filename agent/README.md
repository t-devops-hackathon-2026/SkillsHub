# Google ADK エージェント開発環境の構築

## 1. uv のインストール

[uv](https://docs.astral.sh/uv/) は Python バージョン管理とパッケージ管理を一括で担うツールです。

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows（PowerShell）
powershell -ExecutionPolicy BypassedPolicy -c "irm https://astral.sh/uv/install.ps1 | iex"
```

インストール後、ターミナルを再起動するか、シェルに応じて以下を実行してください。

```bash
source ~/.zshrc   # zsh の場合
source ~/.bashrc  # bash の場合
# Windows は新しい PowerShell を開けば反映されます
```

---

## 2. リポジトリのセットアップ

エージェント開発用のファイルは `agent/` フォルダ内にあります。まずそこに移動してください。

```bash
cd agent/
```

> **以降のコマンドはすべて `agent/` 内で実行してください。**

`.python-version` に `3.12.13` が指定されているので、uv が自動的に対応する Python をダウンロードします。

> バージョンを `3.12.13` に固定している理由: Google ADK は Python `3.11+` を要件としていますが、チーム開発の安定性を重視して `3.12.x`（EOL 2028年10月）を採用しています。

---

## 3. API キーの取得

個人開発では [Google AI Studio](https://aistudio.google.com/) の無料 API キーを使います。料金体系は[こちら](https://ai.google.dev/gemini-api/docs/pricing?hl=ja)を参照してください。

1. [Google AI Studio](https://aistudio.google.com/) にアクセス
2. Google アカウントでサインイン
3. 「Create API Key」をクリック
4. 表示された API キーをコピー（`AIzaSyC...` の形式）

取得したキーを `agent/my_first_agent/.env` ファイルに保存してください。

```bash
GOOGLE_API_KEY=AIzaSyC...
```

> `.env` は `.gitignore` で除外されているので、キーが git に含まれることはありません。

---

## 4. パッケージのインストールと仮想環境の有効化

`uv sync` が仮想環境の作成とパッケージのインストールを一括で行います。  
**`agent/` ディレクトリ内で実行してください。**

```bash
cd agent/  # まだ移動していない場合
uv sync
```

インストール後、仮想環境を有効化してください。

```bash
source .venv/bin/activate        # Mac / Linux
.venv\Scripts\Activate.ps1      # Windows（PowerShell）
```

有効化できるとプロンプトの先頭に `(ai-agent)` と表示されます。

```bash
# 確認
adk --version  # 1.0.0 以上が表示されれば OK
```

> `adk --version` が失敗する場合は、プロンプトに `(ai-agent)` が出ているか確認してください。

---

## パッケージを追加したとき

```bash
uv add <パッケージ名>
```

`pyproject.toml` と `uv.lock` が自動で更新されます。

---

# サンプル AI エージェントの作成と動作確認

環境セットアップ完了後、以下の手順でサンプルエージェントを作成して動作確認できます。

## 1. エージェントの作成

```bash
adk create my_first_agent
```

対話形式でいくつか質問されます。

**モデルの選択：**

```
Choose a model for the root agent:
1. gemini-2.5-flash
2. Other models (fill later)
Choose model (1, 2): 1
```

**バックエンドの選択：**

```
1. Google AI
2. Vertex AI
3. Login with Google
Choose backend (1, 2, 3): 1
```

**API キーの入力：**

```
Don't have API Key? Create one in AI Studio: https://aistudio.google.com/apikey

Enter Google API key: <取得した API キーを入力>
```

> API キーの取得手順は「3. API キーの取得」を参照してください。

---

## 2. 動作確認

```bash
adk web
```

以下のように表示されれば起動成功です。ブラウザで `http://127.0.0.1:8000` を開いて動作を確認できます。

```
INFO:     Started server process
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

終了するには `Ctrl+C` を押してください。
