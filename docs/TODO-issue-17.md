# TODO / 引き継ぎメモ — Issue #17（Searcher / Composer・サービス層）

実装中・実装後に忘れてはいけない項目。チェックが付いていないものは未完了。

## `collect_repo` は最小実装（#41 で本実装に置換予定）

> ⚠️ **#41 が `shared.services.collect_repo` の本実装を所有**する（実 GitHub から収集→差分検知→
> 解析→DB 永続化、引数は `target`=`owner/repo` or `owner`）。本 #17 の `collect_repo(repo_id)` は
> 即時収集ボタン用の**暫定最小版**。#41 着手時に統合し、引数（`repo_id` ↔ `target`）の不整合を
> 解消すること（#16 の `run_collect --repo-id` とも揃える）。

`shared/services.py::collect_repo()` は SKILL.md を取得して Skill を最小カラムで upsert する
ところまで。以下は #14（解析・鮮度）/ #16（統合）/ #41（実 GitHub 配線）が担当する。

- [ ] **構造化解析（AnalyzerAgent）**: 現在 name/description は SKILL.md フロントマターの
  簡易パース（`_parse_frontmatter` / `_first_content_line`）。本来は Gemini で
  name/description/**tags**/usage を構造化抽出する。今は tags/usage を埋めていない。
- [ ] **鮮度判定**: `update_status`（current/stale/needs_update）を commit 経過時間と
  依存変更から判定（仕様「鮮度判定」）。現状は DB 既定の `current` のまま。
- [ ] **埋め込み生成 + dedup 連携**: 収集した Skill を `ai_tools.embed_text` でベクトル化し、
  `run_deduper_for_skill` を呼んで merge 提案まで作る（収集→埋め込み→重複検出の一気通貫）。
- [ ] **`content_hash` による差分スキップ**: 既存 Skill と `content_hash` が同一なら
  解析・埋め込みをスキップ（仕様のコスト対策）。現状は毎回 upsert。
- [ ] **`batch/run_collect.py` への統合**: `collect_repo` 相当を CLI バッチにも実装し、
  サービス層と処理を共有する（即時収集と定期バッチで二重実装にしない）。

## compose 提案の永続化（保存ヘルパは実装済み）

- [x] 保存ヘルパを実装済み: `ai_tools.create_compose_suggestion()` と、その薄いサービス
  ラッパ `services.register_compose_suggestion(compose) -> UUID`。`Suggestion(type=compose)` +
  `suggestion_targets`（候補全件）を保存する。
- [ ] **画面側の配線**: `search_skills()` は提案を**返すだけ**（保存しない）。検索画面（#19）の
  「採用」ボタンから `register_compose_suggestion()` を呼んで保存し、提案レビュー（#20）で
  採用/却下を扱う。

## ADK Runner へのオンライン配線

- [ ] `build_searcher_agent()` / `build_composer_agent()` は ADK 契約用の薄いラッパで MVP では未使用
  （Deduper の `build_deduper_agent` と同方針）。実処理は `run_searcher` / `run_composer` を直接呼ぶ。
  将来オンライン対話を ADK `Runner` に寄せるならここを起点にする。

## 完了条件の本番確認（GCP 必要）

- [ ] `search_skills("議事録 要約")` を Vertex/Gemini 実接続で実行し、出力を Issue #17 にコメント。
  ローカル手順は本リポジトリの実装メモ参照（要 ADC・`DATABASE_URL`・シード＋埋め込み）。
