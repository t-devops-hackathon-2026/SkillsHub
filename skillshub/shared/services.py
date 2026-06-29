from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from skillshub.shared import models
from skillshub.shared.agents.composer import run_composer
from skillshub.shared.agents.searcher import run_searcher
from skillshub.shared.db import get_session
from skillshub.shared.schemas import (
    ComposeSuggestion,
    DashboardSummary,
    SearchResult,
    Skill,
    SuggestionStatus,
    SuggestionType,
    UpdateStatus,
)
from skillshub.shared.tools import ai_tools, github_tools

_MOCK_REPO_ID = UUID("00000000-0000-0000-0000-000000000001")

# get_session は commit/rollback/close を内包するジェネレータ。with で使うため CM 化する。
_session_scope = contextmanager(get_session)

# 合成提案は候補が 2 件以上のときだけ Composer を起動する（仕様: 制御はサービス層に置く）。
_MIN_CANDIDATES_FOR_COMPOSE = 2


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
            s
            for s in result
            if kw in s.name.lower() or kw in s.description.lower() or any(kw in t.lower() for t in s.tags)
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
