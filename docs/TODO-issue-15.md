# TODO / 引き継ぎメモ — Issue #15（埋め込み生成・Deduper Agent）

実装中・実装後に忘れてはいけない項目。チェックが付いていないものは未完了。

## 他 issue と合流時の調整

- [ ] **SequentialAgent への配線（#14 / #16）**
  - `shared/agents/deduper.py::build_deduper_agent()` を `Collector→Analyzer→Embed→Deduper` の SequentialAgent に組み込む。
  - 独立バッチ `batch/run_dedup.py` は将来この中に吸収可能。

- [ ] **`shared/agents/__init__.py` のエクスポート衝突**
  - #14（Collector/Analyzer）マージ時に 1 行 conflict が出たら手で解消。

- [ ] **suggestion 保存ヘルパの一本化**
  - #15 は `ai_tools.create_merge_suggestion` に保存ロジックを閉じ込めた。
  - #14 が `services.py` 側に同等物を作った場合、将来統合を検討。
