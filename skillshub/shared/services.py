from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _build_mock_skills() -> list[Skill]:
    now = datetime.now(UTC)

    def _dt(days_ago: int) -> datetime:
        return now - timedelta(days=days_ago)

    return [
        Skill(
            id=UUID("00000000-0000-0000-0001-000000000001"),
            repo_id=_MOCK_REPO_ID,
            name="議事録要約",
            description="会議の議事録テキストを受け取り、決定事項・論点・ネクストアクションを構造化して要約する。",
            source_path="skills/summarize-minutes/SKILL.md",
            author="yamada",
            tags=["要約", "会議", "NLP"],
            usage="議事録の生テキストを渡すと、Markdownの要約と論点リストを返します。",
            update_status=UpdateStatus.CURRENT,
            last_updated=_dt(30),
            created_at=_dt(90),
            updated_at=_dt(30),
        ),
        Skill(
            id=UUID("00000000-0000-0000-0001-000000000002"),
            repo_id=_MOCK_REPO_ID,
            name="タスク抽出",
            description="文章中からアクションアイテムを抽出し、担当者・期限・優先度を推定して一覧化する。",
            source_path="skills/extract-tasks/SKILL.md",
            author="suzuki",
            tags=["タスク", "会議", "抽出"],
            usage="自由文を渡すと担当者別にタスクを分解して返します。",
            update_status=UpdateStatus.CURRENT,
            last_updated=_dt(37),
            created_at=_dt(100),
            updated_at=_dt(37),
        ),
        Skill(
            id=UUID("00000000-0000-0000-0001-000000000003"),
            repo_id=_MOCK_REPO_ID,
            name="データカタログ取得",
            description="社内データカタログAPIを叩いてテーブル定義・カラム情報を取得し整形して返す。",
            source_path="skills/data-catalog/SKILL.md",
            author="sato",
            tags=["データ", "API", "社内"],
            usage="テーブル名を渡すとスキーマ定義を返します。※参照先APIの仕様変更により要更新。",
            update_status=UpdateStatus.NEEDS_UPDATE,
            last_updated=_dt(109),
            created_at=_dt(200),
            updated_at=_dt(109),
        ),
        Skill(
            id=UUID("00000000-0000-0000-0001-000000000004"),
            repo_id=_MOCK_REPO_ID,
            name="コードレビュー観点",
            description="PRの差分に対して社内のレビュー観点チェックリストを当て、指摘候補を生成する。",
            source_path="skills/review-checklist/SKILL.md",
            author="tanaka",
            tags=["コード", "レビュー", "品質"],
            usage="diffを渡すと観点別に指摘を返します。観点リストの更新が3週間止まっています。",
            update_status=UpdateStatus.STALE,
            last_updated=_dt(73),
            created_at=_dt(150),
            updated_at=_dt(73),
        ),
        Skill(
            id=UUID("00000000-0000-0000-0001-000000000005"),
            repo_id=_MOCK_REPO_ID,
            name="議事録サマリ作成",
            description="ミーティングのメモから要点をまとめ、共有用の短い要約文を作る。",
            source_path="skills/minutes-summary/SKILL.md",
            author="horikoshi",
            tags=["要約", "会議"],
            usage="メモを渡すと3行要約を返します。議事録要約Skillと機能が重複している可能性。",
            update_status=UpdateStatus.STALE,
            last_updated=_dt(145),
            created_at=_dt(200),
            updated_at=_dt(145),
        ),
        Skill(
            id=UUID("00000000-0000-0000-0001-000000000006"),
            repo_id=_MOCK_REPO_ID,
            name="Slack通知整形",
            description="任意のテキストをSlack向けのブロック形式に整形し、見やすい通知を生成する。",
            source_path="skills/slack-format/SKILL.md",
            author="kimura",
            tags=["Slack", "通知", "整形"],
            usage="本文と宛先を渡すとSlackブロックJSONを返します。",
            update_status=UpdateStatus.CURRENT,
            last_updated=_dt(27),
            created_at=_dt(60),
            updated_at=_dt(27),
        ),
    ]


def list_all_tags() -> list[str]:
    return sorted({tag for s in _build_mock_skills() for tag in s.tags})


def list_skills(
    keyword: str = "",
    freshness: str = "",
    tags: list[str] | None = None,
    sort_by: str = "updated",
) -> list[Skill]:
    tags = tags or []
    result = _build_mock_skills()

    if keyword:
        kw = keyword.lower()
        result = [
            s for s in result
            if kw in s.name.lower()
            or kw in s.description.lower()
            or any(kw in t.lower() for t in s.tags)
        ]

    if freshness:
        target_status = UpdateStatus(freshness)
        result = [s for s in result if s.update_status == target_status]

    if tags:
        result = [s for s in result if any(t in s.tags for t in tags)]

    # 現状サポートするソートは sort_by="updated"（更新日順）のみ。
    # 並び順を増やす場合はここに分岐を追加する。
    result = sorted(result, key=lambda s: s.updated_at, reverse=True)

    return result
