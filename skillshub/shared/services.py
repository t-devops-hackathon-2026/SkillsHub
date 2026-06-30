"""アプリ／バッチが使うサービス層。

読み取り系（ダッシュボード）は実 DB を参照する。書き込み系（収集パイプラインの永続化）は
司書バッチ（#9）が使う。``search_skills`` は埋め込み検索（#10/#12）が前提のためモック継続。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skillshub.shared import models
from skillshub.shared.db import _get_engine
from skillshub.shared.schemas import (
    AnalyzedSkill,
    ComposeSuggestion,
    DashboardSummary,
    RawSkill,
    SearchResult,
    SearchResultItem,
    Skill,
    UpdateStatus,
)

_MOCK_REPO_ID = UUID("00000000-0000-0000-0000-000000000001")


# ── 検索（モック継続: 埋め込み検索は #10/#12）──────────────


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
        SearchResultItem(skill=skill_a, confidence=0.92, reason="議事録の要約に特化した Skill です。"),
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


# ── ダッシュボード読み取り（実 DB）────────────────────────


def list_all_tags() -> list[str]:
    with Session(_get_engine()) as session:
        rows = session.scalars(select(models.Skill.tags).order_by(models.Skill.name)).all()
    return sorted({tag for tags in rows for tag in (tags or [])})


def list_skills(
    keyword: str = "",
    update_status: str = "",
    tags: list[str] | None = None,
    sort_by: str = "updated",
) -> list[Skill]:
    tags = tags or []
    with Session(_get_engine()) as session:
        # 現状サポートするソートは更新日順のみ。並び順を増やす場合はここに分岐を追加する。
        stmt = select(models.Skill).order_by(models.Skill.updated_at.desc())
        if update_status:
            stmt = stmt.where(models.Skill.update_status == UpdateStatus(update_status).value)
        rows = session.scalars(stmt).all()
        result = [Skill.model_validate(r) for r in rows]

    if keyword:
        kw = keyword.lower()
        result = [
            s
            for s in result
            if kw in s.name.lower() or kw in s.description.lower() or any(kw in t.lower() for t in s.tags)
        ]
    if tags:
        result = [s for s in result if any(t in s.tags for t in tags)]
    return result


def get_summary() -> DashboardSummary:
    with Session(_get_engine()) as session:
        total = session.scalar(select(func.count()).select_from(models.Skill)) or 0
        needs_update = (
            session.scalar(
                select(func.count())
                .select_from(models.Skill)
                .where(models.Skill.update_status == UpdateStatus.NEEDS_UPDATE.value)
            )
            or 0
        )
        stale = (
            session.scalar(
                select(func.count())
                .select_from(models.Skill)
                .where(models.Skill.update_status == UpdateStatus.STALE.value)
            )
            or 0
        )
        # 重複候補 = merge 提案が指す Skill 数（Deduper=#10 完成まで 0）。
        duplicates = (
            session.scalar(
                select(func.count(func.distinct(models.SuggestionTarget.skill_id)))
                .select_from(models.SuggestionTarget)
                .join(models.Suggestion, models.Suggestion.id == models.SuggestionTarget.suggestion_id)
                .where(models.Suggestion.type == "merge")
            )
            or 0
        )
    return DashboardSummary(
        total_skills=total,
        duplicate_candidates=duplicates,
        needs_update=needs_update,
        stale_count=stale,
    )


# ── 収集パイプラインの永続化（書き込み）────────────────────


def get_existing_content_hashes(repo_id: UUID) -> dict[str, str]:
    """``{source_path: content_hash}`` を返す（Collector の差分検知用）。"""
    with Session(_get_engine()) as session:
        rows = session.execute(
            select(models.Skill.source_path, models.Skill.content_hash)
            .where(models.Skill.repo_id == repo_id)
            .order_by(models.Skill.source_path)
        ).all()
    return {source_path: content_hash for source_path, content_hash in rows if content_hash is not None}


def get_or_create_repository(owner: str, repo: str) -> UUID:
    with Session(_get_engine()) as session:
        existing = session.scalar(
            select(models.Repository).where(models.Repository.owner == owner, models.Repository.repo == repo)
        )
        if existing is not None:
            return existing.id
        repository = models.Repository(owner=owner, repo=repo)
        session.add(repository)
        session.commit()
        return repository.id


def persist_analyzed_skill(
    repo_id: UUID,
    raw: RawSkill,
    analyzed: AnalyzedSkill,
    update_status: UpdateStatus,
    update_draft: str | None,
) -> bool:
    """Skill の upsert と（needs_update 時の）update 提案保存を1トランザクションで行う。

    Skill だけ保存されて提案が欠ける不整合を防ぐため、両書き込みを同一 Session・同一
    コミットにまとめる（途中失敗時はまとめてロールバックされる）。再収集のたびに、同じ
    Skill を指す既存の open な update 提案は一旦すべて dismiss する。これにより
    (1) needs_update 継続時は最新の下書きだけを open に残し、(2) needs_update から
    current/stale へ戻ったときは不要な提案を open のまま残さない。update 提案を新規に
    保存した場合のみ True を返す。
    """
    with Session(_get_engine()) as session:
        skill = session.scalar(
            select(models.Skill).where(
                models.Skill.repo_id == repo_id, models.Skill.source_path == raw.source_path
            )
        )
        is_new = skill is None
        if is_new:
            skill = models.Skill(repo_id=repo_id, source_path=raw.source_path)
            session.add(skill)

        skill.name = analyzed.name
        skill.description = analyzed.description
        skill.tags = analyzed.tags
        skill.usage = analyzed.usage
        skill.author = raw.author
        skill.last_updated = raw.last_commit_at
        skill.update_status = update_status.value
        skill.content_hash = raw.content_hash

        session.flush()  # 新規 Skill の id を採番 / 既存提案の探索にも id が要る

        # 同じ Skill を指す既存の open な update 提案は、今回のステータスに関わらず一旦 dismiss する。
        # needs_update 継続時は直後に最新の下書きを open で作り直し、current/stale へ戻ったときは
        # 不要な提案を open のまま残さない。新規 Skill には既存提案が無いのでスキップ。
        if not is_new:
            stale_updates = session.scalars(
                select(models.Suggestion)
                .join(models.SuggestionTarget, models.SuggestionTarget.suggestion_id == models.Suggestion.id)
                .where(
                    models.SuggestionTarget.skill_id == skill.id,
                    models.Suggestion.type == "update",
                    models.Suggestion.status == "open",
                )
                .order_by(models.Suggestion.created_at)
            ).all()
            for stale in stale_updates:
                stale.status = "dismissed"

        saved_suggestion = False
        if update_status is UpdateStatus.NEEDS_UPDATE and update_draft:
            suggestion = models.Suggestion(type="update", content=update_draft, status="open")
            session.add(suggestion)
            session.flush()  # suggestion の id 採番後にブリッジを張る
            session.add(models.SuggestionTarget(suggestion_id=suggestion.id, skill_id=skill.id))
            saved_suggestion = True

        session.commit()
        return saved_suggestion


# ── 収集パイプラインのエントリ ────────────────────────────


def collect_local(root: Path, owner: str = "local", repo: str = "samples") -> dict[str, object]:
    """ローカル samples を収集・解析し、DB に永続化する（#9 デモ／ローカルモード）。

    GitHub モードの収集（``collect_repo``）と置き換え可能な形に揃えてある。
    """
    import asyncio

    from skillshub.shared.agents.librarian import AnalyzedResult, collect_and_analyze
    from skillshub.shared.sources.local import load_local_skills

    repo_id = get_or_create_repository(owner, repo)

    results: list[AnalyzedResult] = asyncio.run(
        collect_and_analyze(
            load_raw_skills=lambda: load_local_skills(root),
            load_existing_hashes=lambda: get_existing_content_hashes(repo_id),
        )
    )

    needs_update = 0
    for r in results:
        if persist_analyzed_skill(repo_id, r.raw, r.analyzed, r.update_status, r.update_draft):
            needs_update += 1

    return {
        "repo_id": str(repo_id),
        "processed_skills": len(results),
        "needs_update": needs_update,
        "results": results,
    }
