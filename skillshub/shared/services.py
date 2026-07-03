"""アプリ／バッチが使うサービス層。

読み取り系（ダッシュボード）は実 DB を参照する。書き込み系（収集パイプラインの永続化）は
司書バッチが使う。``search_skills`` は埋め込み検索を用いた Searcher→Composer で
候補と合成提案を返す。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from skillshub.shared import models
from skillshub.shared.agents.composer import run_composer
from skillshub.shared.agents.searcher import run_searcher
from skillshub.shared.db import _get_engine, session_scope
from skillshub.shared.schemas import (
    AnalyzedSkill,
    ComposeSuggestion,
    DashboardSummary,
    RawSkill,
    SearchResult,
    Skill,
    SkillDetail,
    SuggestionStatus,
    SuggestionTargetRef,
    SuggestionType,
    SuggestionView,
    UpdateStatus,
)
from skillshub.shared.tools import ai_tools

if TYPE_CHECKING:
    from skillshub.shared.agents.librarian import LibrarianRunResult

# テスト（test_services.py）が monkeypatch する差し替え点なので、モジュール属性として保持する。
_session_scope = session_scope

# 合成提案は候補が 2 件以上のときだけ Composer を起動する（仕様: 制御はサービス層に置く）。
_MIN_CANDIDATES_FOR_COMPOSE = 2

# GitHub 上に実在しない「擬似 owner」の規約。
# LOCAL_OWNER はローカル samples 収集（collect_local）、MANUAL_OWNER は手動登録 Skill の
# 置き場（seed.py）で、いずれも GitHub 収集・リンク生成の対象にしない。
LOCAL_OWNER = "local"
MANUAL_OWNER = "internal"
PSEUDO_OWNERS = frozenset({LOCAL_OWNER, MANUAL_OWNER})


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

    検索画面の「採用」操作から呼ぶ想定。``search_skills`` は提案を返すだけで保存せず、
    保存はこのユーザー操作に限定する（採用/却下の管理は提案レビュー画面で扱う）。
    """
    with _session_scope() as session:
        return ai_tools.create_compose_suggestion(session, compose)


def collect_repo(repo_id: str, *, embed_fn: ai_tools.EmbeddingFn | None = None) -> dict[str, object]:
    """指定リポジトリから GitHub 経由で全 Skill を収集し、フルパイプラインで DB に保存する。

    収集→差分検知→解析→鮮度判定→埋め込み→重複検出 の全工程を実行する。
    GitHub App 認証（環境変数 GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY または Secret Manager）が必要。
    """
    from skillshub.shared.sources.github import load_github_skills

    repo_uuid = UUID(repo_id)
    with _session_scope() as session:
        repo = session.get(models.Repository, repo_uuid)
        if repo is None:
            raise ValueError(f"リポジトリが見つかりません: {repo_id}")
        if not repo.repo:
            # repo="" は Organization の登録行（マーカー）。単一リポジトリとしては収集できない。
            raise ValueError(f"{repo.owner} は Organization 登録のため collect_org で収集してください")
        target = f"{repo.owner}/{repo.repo}"

    run_result = _run_collection(repo_uuid, lambda: load_github_skills(target), embed_fn=embed_fn)
    return _collection_summary(repo_uuid, run_result)


def github_app_configured() -> bool:
    """GitHub App の認証情報が使える見込みかを環境変数で判定する（登録画面の出し分け用）。

    ローカルは環境変数、本番は Secret Manager（``GOOGLE_CLOUD_PROJECT`` 必須）で解決される
    （cf. ``github_tools._resolve_app_id``）。どちらかの経路が立っていれば True。
    実際に認証が通るかまでは確認しない。
    """
    has_env_creds = bool(os.environ.get("GITHUB_APP_ID")) and bool(
        os.environ.get("GITHUB_APP_PRIVATE_KEY") or os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")
    )
    return has_env_creds or bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))


def list_github_scope() -> dict[str, list[str]]:
    """GitHub App が閲覧できる範囲を ``{アカウント名: [owner/repo, ...]}`` で返す。

    登録画面の選択肢に使う。App がインストールされた Org / ユーザーごとに、
    アクセス可能な全リポジトリを列挙する。
    """
    from skillshub.shared.tools import github_tools

    app_jwt = github_tools.generate_app_jwt()
    scope: dict[str, list[str]] = {}
    for installation_id, account in github_tools.list_app_installations(app_jwt):
        token = github_tools.get_installation_token(app_jwt, installation_id)
        scope[account] = sorted(
            f"{repo_owner}/{repo_name}"
            for repo_owner, repo_name, _default_branch in github_tools.list_installation_repositories(token)
        )
    return scope


@dataclass(frozen=True)
class OrgCollectResult:
    """Organization 一括収集（``collect_org``）の集計結果。"""

    owner: str
    repo_ids: list[str] = field(default_factory=list)
    collected_skills: int = 0
    skipped_skills: int = 0
    failed_repos: list[str] = field(default_factory=list)


def collect_org(owner: str, *, embed_fn: ai_tools.EmbeddingFn | None = None) -> OrgCollectResult:
    """Organization 配下の全アクセス可能リポジトリを収集し、集計を返す。

    GitHub App のインストール配下を列挙して未登録リポジトリを自動登録し、1 リポジトリずつ
    収集パイプラインを実行する（1 件の失敗で残りを止めない。失敗は結果とログで扱う）。
    """
    from skillshub.shared.sources.github import load_github_skills
    from skillshub.shared.tools import github_tools

    app_jwt = github_tools.generate_app_jwt()
    installation_id = github_tools.get_installation_id_for_org(app_jwt, owner)
    token = github_tools.get_installation_token(app_jwt, installation_id)

    repo_ids: list[str] = []
    failed_repos: list[str] = []
    collected = 0
    skipped = 0
    for repo_owner, repo_name, _default_branch in github_tools.list_installation_repositories(token):
        repo_uuid = get_or_create_repository(repo_owner, repo_name)
        repo_ids.append(str(repo_uuid))
        target = f"{repo_owner}/{repo_name}"
        try:
            run_result = _run_collection(repo_uuid, partial(load_github_skills, target), embed_fn=embed_fn)
        except Exception:  # noqa: BLE001 — 1 リポジトリの失敗で Org 全体を止めない
            failed_repos.append(target)
            continue
        collected += run_result.stats.collected
        skipped += run_result.stats.skipped

    return OrgCollectResult(
        owner=owner,
        repo_ids=repo_ids,
        collected_skills=collected,
        skipped_skills=skipped,
        failed_repos=failed_repos,
    )


def _run_collection(
    repo_id: UUID,
    load_raw_skills: Callable[[], list[RawSkill]],
    *,
    embed_fn: ai_tools.EmbeddingFn | None,
) -> LibrarianRunResult:
    """1 リポジトリ分の収集パイプラインを実行し、成功時に ``last_collected_at`` を更新する。

    GitHub（``collect_repo``）・ローカル（``collect_local``）の両収集源から使う共通部。
    librarian の import は ADK 依存を遅延させるため関数内で行う。
    """
    from skillshub.shared.agents.librarian import run_librarian_for_repo

    run_result = run_librarian_for_repo(
        repo_id,
        load_raw_skills=load_raw_skills,
        load_existing_hashes=lambda: get_existing_content_hashes(repo_id),
        embed_fn=embed_fn,
    )
    touch_last_collected_at(repo_id)
    return run_result


def _collection_summary(repo_id: UUID, run_result: LibrarianRunResult) -> dict[str, object]:
    """収集結果を呼び出し側（画面・バッチ・デモ）が使う共通の dict 形式にまとめる。"""
    stats = run_result.stats
    return {
        "repo_id": str(repo_id),
        "collected_skills": stats.collected,
        "skipped_skills": stats.skipped,
        "needs_update": stats.needs_update,
        "merge_suggestions": stats.merge_suggestions,
        "failed": stats.failed,
        "status": "success",
        "results": run_result.results,
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


# ── ダッシュボード読み取り（実 DB）────────────────────────


def list_repositories() -> list[dict[str, object]]:
    """登録済みリポジトリ一覧を Skill 件数付きで返す（リポジトリ登録画面用）。"""
    with Session(_get_engine()) as session:
        rows = session.execute(
            select(models.Repository, func.count(models.Skill.id))
            .outerjoin(models.Skill, models.Skill.repo_id == models.Repository.id)
            .group_by(models.Repository.id)
            .order_by(models.Repository.created_at)
        ).all()
    return [
        {
            "id": str(repo.id),
            "owner": repo.owner,
            "repo": repo.repo,
            "last_collected_at": repo.last_collected_at,
            "skill_count": skill_count,
        }
        for repo, skill_count in rows
    ]


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


# ── Skill 詳細・提案レビュー ───────────────────────────────


def _to_suggestion_view(session: Session, suggestion: models.Suggestion) -> SuggestionView:
    """Suggestion 行を、対象 Skill 名込みのビューへ変換する。

    提案は多くても数十件の想定なので、targets は提案ごとに引く（JOIN の作り込みはしない）。
    """
    target_rows = session.execute(
        select(models.SuggestionTarget.skill_id, models.Skill.name)
        .join(models.Skill, models.Skill.id == models.SuggestionTarget.skill_id)
        .where(models.SuggestionTarget.suggestion_id == suggestion.id)
        .order_by(models.Skill.name)
    ).all()
    return SuggestionView(
        id=suggestion.id,
        type=SuggestionType(suggestion.type),
        content=suggestion.content,
        status=SuggestionStatus(suggestion.status),
        created_at=suggestion.created_at,
        targets=[SuggestionTargetRef(skill_id=skill_id, skill_name=name) for skill_id, name in target_rows],
    )


def get_skill(skill_id: str) -> SkillDetail | None:
    """Skill 詳細画面用に、Skill 本体・取得元リポジトリ・open な提案をまとめて返す。

    見つからなければ ``None``（DB 初期化後に古い id で遷移してきたケースを画面側で拾う）。
    """
    with _session_scope() as session:
        skill = session.get(models.Skill, UUID(skill_id))
        if skill is None:
            return None
        suggestions = session.scalars(
            select(models.Suggestion)
            .join(models.SuggestionTarget, models.SuggestionTarget.suggestion_id == models.Suggestion.id)
            .where(
                models.SuggestionTarget.skill_id == skill.id,
                models.Suggestion.status == SuggestionStatus.OPEN,
            )
            .order_by(models.Suggestion.created_at.desc())
        ).all()
        return SkillDetail(
            skill=Skill.model_validate(skill),
            repo_owner=skill.repository.owner,
            repo_name=skill.repository.repo,
            open_suggestions=[_to_suggestion_view(session, s) for s in suggestions],
        )


def list_suggestions(status: SuggestionStatus = SuggestionStatus.OPEN) -> list[SuggestionView]:
    """指定ステータスの提案を新しい順に返す（提案レビュー画面用）。"""
    with _session_scope() as session:
        suggestions = session.scalars(
            select(models.Suggestion)
            .where(models.Suggestion.status == status)
            .order_by(models.Suggestion.created_at.desc())
        ).all()
        return [_to_suggestion_view(session, s) for s in suggestions]


def accept_suggestion(suggestion_id: str) -> None:
    """提案を採用（status→accepted）する。

    仕様（step1.md「提案の採用時挙動」）:
    - merge / compose: status 更新のみ（GitHub への反映は作者が手元で行う）。
    - update: content の diff を適用済みドラフトとして残し、対象 Skill の
      ``update_status`` を ``current`` に戻す。

    open でない提案には何もしない（再描画中の二度押しをエラーにしない）。
    """
    with _session_scope() as session:
        suggestion = session.get(models.Suggestion, UUID(suggestion_id))
        if suggestion is None:
            raise ValueError(f"提案が見つかりません: {suggestion_id}")
        if suggestion.status != SuggestionStatus.OPEN:
            return
        suggestion.status = SuggestionStatus.ACCEPTED
        if suggestion.type == SuggestionType.UPDATE:
            for target in suggestion.targets:
                skill = session.get(models.Skill, target.skill_id)
                if skill is not None:
                    skill.update_status = UpdateStatus.CURRENT.value


def dismiss_suggestion(suggestion_id: str) -> None:
    """提案を却下（status→dismissed）する。open でない提案には何もしない。"""
    with _session_scope() as session:
        suggestion = session.get(models.Suggestion, UUID(suggestion_id))
        if suggestion is None:
            raise ValueError(f"提案が見つかりません: {suggestion_id}")
        if suggestion.status != SuggestionStatus.OPEN:
            return
        suggestion.status = SuggestionStatus.DISMISSED


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


def _persist_analyzed_skill(
    session: Session,
    repo_id: UUID,
    raw: RawSkill,
    analyzed: AnalyzedSkill,
    update_status: UpdateStatus,
    update_draft: str | None,
) -> tuple[models.Skill, bool]:
    """Skill の upsert と（needs_update 時の）update 提案保存を、与えられた session 上で行う。

    コミットはしない（呼び出し側の責務）。収集パイプライン（``librarian``）では、この後の
    埋め込み生成・重複検出と同一トランザクションにまとめたいため、session を外から受け取る。
    永続化した Skill と、update 提案を新規保存したかどうか（bool）を返す。

    再収集のたびに、同じ Skill を指す既存の open な update 提案は一旦すべて dismiss する。
    これにより (1) needs_update 継続時は最新の下書きだけを open に残し、(2) needs_update から
    current/stale へ戻ったときは不要な提案を open のまま残さない。
    """
    skill = session.scalar(
        select(models.Skill).where(models.Skill.repo_id == repo_id, models.Skill.source_path == raw.source_path)
    )
    is_new = skill is None
    if skill is None:
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

    # dismiss の方針は docstring 参照。新規 Skill には既存提案が無いのでスキップ。
    if not is_new:
        stale_updates = session.scalars(
            select(models.Suggestion)
            .join(models.SuggestionTarget, models.SuggestionTarget.suggestion_id == models.Suggestion.id)
            .where(
                models.SuggestionTarget.skill_id == skill.id,
                models.Suggestion.type == SuggestionType.UPDATE,
                models.Suggestion.status == SuggestionStatus.OPEN,
            )
            .order_by(models.Suggestion.created_at)
        ).all()
        for stale in stale_updates:
            stale.status = SuggestionStatus.DISMISSED

    saved_suggestion = False
    if update_status is UpdateStatus.NEEDS_UPDATE and update_draft:
        suggestion = models.Suggestion(type=SuggestionType.UPDATE, content=update_draft, status=SuggestionStatus.OPEN)
        session.add(suggestion)
        session.flush()  # suggestion の id 採番後にブリッジを張る
        session.add(models.SuggestionTarget(suggestion_id=suggestion.id, skill_id=skill.id))
        saved_suggestion = True

    return skill, saved_suggestion


def persist_analyzed_skill(
    repo_id: UUID,
    raw: RawSkill,
    analyzed: AnalyzedSkill,
    update_status: UpdateStatus,
    update_draft: str | None,
) -> bool:
    """単体の Skill 永続化（自前 session で1トランザクション）。

    Skill だけ保存されて提案が欠ける不整合を防ぐため、Skill と update 提案の書き込みを
    同一コミットにまとめる（途中失敗時はまとめてロールバック）。update 提案を新規に保存した
    場合のみ True を返す。収集パイプラインからは埋め込み・重複検出と同一 session にまとめたい
    ため ``_persist_analyzed_skill`` を直接使う。
    """
    with Session(_get_engine()) as session:
        _skill, saved_suggestion = _persist_analyzed_skill(session, repo_id, raw, analyzed, update_status, update_draft)
        session.commit()
        return saved_suggestion


# ── 収集パイプラインのエントリ ────────────────────────────


def touch_last_collected_at(repo_id: UUID) -> None:
    """リポジトリの ``last_collected_at`` を現在時刻へ更新する（収集成功時に呼ぶ）。

    仕様（er.md）どおり収集の失敗は status カラムでは扱わず、成功時の最終収集時刻と
    構造化ログだけで運用する。
    """
    with _session_scope() as session:
        repo = session.get(models.Repository, repo_id)
        if repo is not None:
            repo.last_collected_at = datetime.now(UTC)


def collect_local(
    root: Path,
    owner: str = "local",
    repo: str = "samples",
    *,
    embed_fn: ai_tools.EmbeddingFn | None = None,
) -> dict[str, object]:
    """ローカル samples を収集・解析し、埋め込み・重複検出まで含めて DB に永続化する。

    GitHub モードの収集（``collect_repo``）と同じ共通部（``_run_collection``）に
    ローカル収集源を渡すだけの薄いラッパ。``embed_fn`` 未指定なら Vertex AI
    （テスト・オフラインでは決定論フェイクを注入）。
    """
    from skillshub.shared.sources.local import load_local_skills

    repo_id = get_or_create_repository(owner, repo)
    run_result = _run_collection(repo_id, lambda: load_local_skills(root), embed_fn=embed_fn)
    return _collection_summary(repo_id, run_result)
