"""Analyzer まわりの純関数（LLM 非依存）のテスト。"""

from __future__ import annotations

from skillshub.shared.agents.analyzer import format_update_draft
from skillshub.shared.schemas import UpdateDraft


def test_format_update_draft_embeds_diff_in_fence() -> None:
    draft = UpdateDraft(
        situation="Data Catalog API v1 は廃止予定です。",
        proposal="v2 のエンドポイントと OAuth 2.0 へ更新してください。",
        diff="--- a/SKILL.md\n+++ b/SKILL.md\n-v1\n+v2",
    )
    content = format_update_draft(draft)
    assert content.startswith("**状況:** Data Catalog API v1 は廃止予定です。")
    assert "**提案:** v2 のエンドポイントと OAuth 2.0 へ更新してください。" in content
    assert "```diff\n--- a/SKILL.md\n+++ b/SKILL.md\n-v1\n+v2\n```" in content


def test_format_update_draft_strips_model_added_fences() -> None:
    """モデルが指示に反して diff をフェンスで包んできても二重フェンスにしない。"""
    draft = UpdateDraft(
        situation="s",
        proposal="p",
        diff="```diff\n-old\n+new\n```",
    )
    content = format_update_draft(draft)
    assert content.count("```") == 2  # 開始と終了の1組だけ
    assert "```diff\n-old\n+new\n```" in content
