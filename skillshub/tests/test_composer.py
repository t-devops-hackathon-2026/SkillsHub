"""ComposerAgent（合成提案）のテスト。

LLM 生成は注入（``generate_fn``）で差し替え、GCP に触らず分岐とマッピングを検証する。
DB も不要。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from skillshub.shared.agents.composer import ComposerWorkflow, run_composer
from skillshub.shared.schemas import SearchResultItem, Skill, UpdateStatus


def _item(name: str) -> SearchResultItem:
    now = datetime.now(UTC)
    skill = Skill(
        id=uuid4(),
        repo_id=uuid4(),
        name=name,
        description=f"{name}の説明",
        source_path=f"skills/{name}/SKILL.md",
        author=None,
        tags=[],
        usage=None,
        update_status=UpdateStatus.CURRENT,
        last_updated=now,
        content_hash=None,
        created_at=now,
        updated_at=now,
    )
    return SearchResultItem(skill=skill, confidence=0.9, reason="r")


def test_run_composer_returns_suggestion_for_two_candidates() -> None:
    items = [_item("議事録要約"), _item("タスク抽出")]

    def fake_generate(query: str, candidates: list[SearchResultItem], model: str) -> ComposerWorkflow:
        return ComposerWorkflow(title="統合ワークフロー", body="要約してからタスク抽出する")

    result = run_composer("議事録からタスクを作りたい", items, generate_fn=fake_generate)

    assert result is not None
    assert result.title == "統合ワークフロー"
    # 対象 Skill は候補全件が機械的に埋まる（LLM には生成させない）。
    assert result.target_skill_ids == [items[0].skill.id, items[1].skill.id]


def test_run_composer_returns_none_for_single_candidate() -> None:
    items = [_item("議事録要約")]

    result = run_composer(
        "x",
        items,
        generate_fn=lambda q, c, m: ComposerWorkflow(title="t", body="b"),
    )
    assert result is None


def test_run_composer_returns_none_when_llm_judges_not_needed() -> None:
    """単一 Skill で足りると LLM が判断した場合（needed=False）は合成提案を出さない。"""
    items = [_item("議事録要約"), _item("タスク抽出")]

    result = run_composer(
        "議事録を要約したい",
        items,
        generate_fn=lambda q, c, m: ComposerWorkflow(needed=False, title="", body=""),
    )
    assert result is None


def test_run_composer_returns_none_on_generate_error() -> None:
    items = [_item("a"), _item("b")]

    def boom(query: str, candidates: list[SearchResultItem], model: str) -> ComposerWorkflow:
        raise RuntimeError("LLM down")

    assert run_composer("x", items, generate_fn=boom) is None
