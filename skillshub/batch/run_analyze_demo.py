"""ローカル samples を解析して構造化結果＋鮮度を出すデモ。

ローカル samples/ の SKILL.md を Collector → Analyzer → 鮮度判定 のパイプラインに通し、
構造化 JSON と鮮度を print する。さらに DB に永続化するので、続けて Streamlit を起動すると
ダッシュボードに収集 Skill が並ぶ。

前提:
    - postgres が起動し alembic migrate 済み（`docker compose up -d` → `bash scripts/migrate.sh`）
    - Gemini 認証（`.env` に GOOGLE_API_KEY、または GOOGLE_GENAI_USE_VERTEXAI=TRUE ＋ project）

実行:
    uv run python -m skillshub.batch.run_analyze_demo

ローカルにコミット日が無いため、鮮度デモ用に各サンプルの mtime を擬似的に調整する
（current / stale を再現。needs_update は SKILL.md 本文の古さ兆候で誘発）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from skillshub.shared import services

_SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "samples"

# 鮮度を再現するための擬似コミット経過日数（mtime に反映）。
_SAMPLE_AGE_DAYS: dict[str, int] = {
    "meeting-summarizer": 10,  # → current
    "weekly-report": 120,  # → stale
    "data-catalog": 30,  # 本文に deprecated 記述 → needs_update（日数は若くても兆候優先）
}


def _backdate_samples(root: Path) -> None:
    """各サンプル SKILL.md の mtime を擬似経過日数に合わせて調整する（ローカルデモ専用）。"""
    now = time.time()
    for skill_dir, age_days in _SAMPLE_AGE_DAYS.items():
        skill_md = root / "skills" / skill_dir / "SKILL.md"
        if skill_md.exists():
            ts = now - age_days * 86400
            os.utime(skill_md, (ts, ts))


def main() -> int:
    root = _SAMPLES_ROOT
    _backdate_samples(root)

    print(f"=== ローカル収集: {root} ===")
    result = services.collect_local(root)

    processed = result["results"]
    print(f"処理 Skill 数: {result['collected_skills']} / needs_update: {result['needs_update']}\n")

    if not processed:
        print("（変更分なし: 前回と content_hash が同一のためスキップされました）")
        return 0

    for r in processed:  # type: ignore[attr-defined]
        record = {
            "source_path": r.raw.source_path,
            "update_status": r.update_status.value,
            "analyzed": r.analyzed.model_dump(),
        }
        print(json.dumps(record, ensure_ascii=False, indent=2))
        if r.update_draft:
            print("--- update 提案（diff 下書き）---")
            print(r.update_draft)
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
