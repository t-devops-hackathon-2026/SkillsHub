from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from skillshub.shared.schemas import (
    ComposeSuggestion,
    DashboardSummary,
    SearchResult,
    SearchResultItem,
    Skill,
    UpdateStatus,
)

_MOCK_REPO_ID = UUID("00000000-0000-0000-0000-000000000001")


def search_skills(query: str) -> SearchResult:
    now = datetime.now(UTC)
    skill_a = Skill(
        id=uuid4(),
        repo_id=_MOCK_REPO_ID,
        name="議事録要約 Skill",
        description="会議の議事録を自動で要約し、要点を箇条書きにする",
        source_path="skills/meeting-summarizer/SKILL.md",
        author="alice",
        tags=["議事録", "要約", "会議"],
        usage="会議の議事録テキストを入力すると、要点を3〜5行に要約します。",
        update_status=UpdateStatus.CURRENT,
        last_updated=now,
        created_at=now,
        updated_at=now,
    )
    skill_b = Skill(
        id=uuid4(),
        repo_id=_MOCK_REPO_ID,
        name="タスク抽出 Skill",
        description="会議メモやチャットログからアクションアイテムを抽出する",
        source_path="skills/task-extractor/SKILL.md",
        author="bob",
        tags=["タスク", "抽出", "会議"],
        usage="テキストを入力すると、TODO / アクションアイテムをリストアップします。",
        update_status=UpdateStatus.STALE,
        last_updated=now,
        created_at=now,
        updated_at=now,
    )

    items = [
        SearchResultItem(
            skill=skill_a,
            confidence=0.92,
            reason="議事録の要約に特化した Skill です。",
        ),
        SearchResultItem(
            skill=skill_b,
            confidence=0.78,
            reason="会議メモからタスクを抽出するため、要約と組み合わせると効果的です。",
        ),
    ]

    compose = ComposeSuggestion(
        title="議事録要約 + タスク抽出 ワークフロー",
        body="議事録を要約した後、タスクを抽出するワークフローを提案します。",
        target_skill_ids=[skill_a.id, skill_b.id],
    )

    return SearchResult(items=items, compose_suggestion=compose)


def collect_repo(repo_id: str) -> dict[str, object]:
    return {
        "repo_id": repo_id,
        "collected_skills": 3,
        "new_skills": 1,
        "updated_skills": 2,
        "status": "success",
    }


def get_summary() -> DashboardSummary:
    return DashboardSummary(
        total_skills=12,
        duplicate_candidates=2,
        needs_update=3,
        stale_count=4,
    )
