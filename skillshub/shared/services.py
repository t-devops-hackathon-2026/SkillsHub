"""アプリ／バッチが使うサービス層。

読み取り系（ダッシュボード）は実 DB を参照する。書き込み系（収集パイプラインの永続化）は
司書バッチ（#9）が使う。``search_skills`` は埋め込み検索（#10/#12）を用いた Searcher→Composer で
候補と合成提案を返す。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from skillshub.shared import models
from skillshub.shared.agents.composer import run_composer
from skillshub.shared.agents.searcher import run_searcher
from skillshub.shared.db import _get_engine, get_session
from skillshub.shared.schemas import (
    AnalyzedSkill,
    ComposeSuggestion,
    DashboardSummary,
    RawSkill,
    SearchResult,
    Skill,
    SuggestionStatus,
    SuggestionType,
    UpdateStatus,
)
from skillshub.shared.tools import ai_tools, github_tools

# get_session は commit/rollback/close を内包するジェネレータ。with で使うため CM 化する。
_session_scope = contextmanager(get_session)

# 合成提案は候補が 2 件以上のときだけ Composer を起動する（仕様: 制御はサービス層に置く）。
_MIN_CANDIDATES_FOR_COMPOSE = 2


# ── 検索（Searcher → Composer）──────────────────────────


def search_skills(query: str) -> SearchResult:
    """自然文クエリで Skill を検索し、候補（確信度・推薦理由つき）と合成提案を返す。

    仕様（step1.md「自然言語検索」）どおり Searcher→Composer を機械的に直列化せず、
    候補が 2 件以上のときだけ Composer を起動する。Composer の生成は失敗しても
    ``None`` になるだけで、候補リスト自体は必ず返る。
    """
    with _session_scope() as session:
        items = run_searcher(session, query)

    # 候補は schemas（セッション非依存）なので、合成生成はセッションを閉じてから行う。
    compose = run_composer(query, items) if len(items) >= _MIN_CANDIDATES_FOR_COMPOSE else None
    return SearchResult(items=items, compose_suggestion=compose)


def register_compose_suggestion(compose: ComposeSuggestion) -> UUID:
    """合成提案を suggestion(type=compose) として保存し、新規 id を返す。

    検索画面（#19）の「採用」操作から呼ぶ想定。``search_skills`` は提案を返すだけで保存せず、
    保存はこのユーザー操作に限定する（提案レビュー画面 #20 で採用/却下を扱う）。
    """
    with _session_scope() as session:
        return ai_tools.create_compose_suggestion(session, compose)


def collect_repo(repo_id: str) -> dict[str, object]:
    """指定リポジトリから SKILL.md を即時収集し、Skill を最小カラムで DB へ upsert する。

    最小実装（Issue #17）。name/description は SKILL.md のフロントマターを簡易抽出する。
    構造化解析（AnalyzerAgent）・鮮度判定・埋め込み生成・dedup 連携は未実装で、
    司書バッチ（run_collect）側の課題として docs/TODO-issue-17.md に切り出している。

    GitHub App 認証（環境変数 / Secret Manager）が必要。
    """
    repo_uuid = UUID(repo_id)
    new_skills = 0
    updated_skills = 0

    with _session_scope() as session:
        repo = session.get(models.Repository, repo_uuid)
        if repo is None:
            raise ValueError(f"リポジトリが見つかりません: {repo_id}")

        collected = github_tools.collect_skills(f"{repo.owner}/{repo.repo}")
        for cs in collected:
            name, description = _extract_name_description(cs)
            existing = session.scalar(
                select(models.Skill)
                .where(models.Skill.repo_id == repo.id)
                .where(models.Skill.source_path == cs.source_path)
            )
            if existing is None:
                session.add(
                    models.Skill(
                        repo_id=repo.id,
                        name=name,
                        description=description,
                        source_path=cs.source_path,
                        author=cs.author,
                        last_updated=cs.last_commit_at,
                        content_hash=cs.content_hash,
                    )
                )
                new_skills += 1
            else:
                existing.name = name
                existing.description = description
                existing.author = cs.author
                existing.last_updated = cs.last_commit_at
                existing.content_hash = cs.content_hash
                updated_skills += 1

        repo.last_collected_at = datetime.now(UTC)
        collected_count = len(collected)

    return {
        "repo_id": repo_id,
        "collected_skills": collected_count,
        "new_skills": new_skills,
        "updated_skills": updated_skills,
        "status": "success",
    }


def get_summary() -> DashboardSummary:
    """ダッシュボードのサマリ（総数 / 重複候補 / 要更新 / 陳腐化注意）を SQL 集計で返す。"""
    with _session_scope() as session:
        total_skills = _count(session, select(func.count()).select_from(models.Skill))
        duplicate_candidates = _count(
            session,
            select(func.count())
            .select_from(models.Suggestion)
            .where(models.Suggestion.type == SuggestionType.MERGE)
            .where(models.Suggestion.status == SuggestionStatus.OPEN),
        )
        needs_update = _count(
            session,
            select(func.count())
            .select_from(models.Skill)
            .where(models.Skill.update_status == UpdateStatus.NEEDS_UPDATE),
        )
        stale_count = _count(
            session,
            select(func.count()).select_from(models.Skill).where(models.Skill.update_status == UpdateStatus.STALE),
        )

    return DashboardSummary(
        total_skills=total_skills,
        duplicate_candidates=duplicate_candidates,
        needs_update=needs_update,
        stale_count=stale_count,
    )


def _count(session: Session, stmt: Select[tuple[int]]) -> int:
    """COUNT クエリを実行し、結果（NULL なら 0）を int で返す。"""
    return int(session.scalar(stmt) or 0)


def _extract_name_description(collected: github_tools.CollectedSkill) -> tuple[str, str]:
    """SKILL.md のフロントマターから name/description を簡易抽出する（最小実装）。

    本来は AnalyzerAgent が Gemini で構造化解析する範囲。ここでは YAML フロントマターの
    ``name:`` / ``description:`` だけを軽量パースし、無ければディレクトリ名・先頭行に
    フォールバックする（docs/TODO-issue-17.md）。
    """
    text = collected.skill_md.text
    front = _parse_frontmatter(text)
    name = front.get("name") or collected.skill_dir.rsplit("/", 1)[-1] or collected.source_path
    description = front.get("description") or _first_content_line(text)
    return name, description


def _parse_frontmatter(text: str) -> dict[str, str]:
    """先頭の ``---`` で囲まれた YAML フロントマターを ``key: value`` で軽量パースする。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    front: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            front[key.strip()] = value.strip().strip("\"'")
    return front


def _first_content_line(text: str) -> str:
    """フロントマター / 見出し記号を除いた最初の本文行を返す（description の代替）。"""
    in_frontmatter = False
    for index, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if index == 0 and line == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
            continue
        stripped = line.lstrip("#").strip()
        if stripped:
            return stripped
    return ""


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
            select(models.Skill).where(models.Skill.repo_id == repo_id, models.Skill.source_path == raw.source_path)
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
