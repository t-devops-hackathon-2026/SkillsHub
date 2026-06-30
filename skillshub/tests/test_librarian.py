"""司書統括（収集→解析→永続化→埋め込み→重複検出）のテスト。

収集源リゾルバ（``run_collect._resolve_loader``）は DB 不要の純ロジック。永続化＋重複検出
（``persist_and_dedup``）は実 pgvector が要るため ``db_session`` fixture 経由（``DATABASE_URL``
未設定時は自動スキップ）。埋め込みは決定論フェイク（``fake_embed_fn``）を注入し GCP 認証なしで
回す。LLM を使う収集・解析（``collect_and_analyze``）は本テストの対象外で、analyzer / collector
の各テストでカバーする。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skillshub.batch import run_collect
from skillshub.shared.agents.librarian import AnalyzedResult, persist_and_dedup
from skillshub.shared.models import Repository, Skill, SkillEmbedding, Suggestion
from skillshub.shared.schemas import AnalyzedSkill, RawSkill, SuggestionType, UpdateStatus

# ── 収集源リゾルバ（DB 不要）──────────────────────────────


def test_resolve_loader_local_returns_loader() -> None:
    assert run_collect._resolve_loader("local", "samples") is not None


def test_resolve_loader_github_returns_loader() -> None:
    # GitHub 収集源（#41）: ローカル以外は GitHub ローダーを返す（呼び出し時に GitHub API へ接続する）。
    assert run_collect._resolve_loader("some-org", "some-repo") is not None


# ── persist_and_dedup（DB 統合）──────────────────────────


def _result(
    source_path: str,
    content_hash: str,
    name: str,
    description: str,
    *,
    status: UpdateStatus = UpdateStatus.CURRENT,
    draft: str | None = None,
) -> AnalyzedResult:
    raw = RawSkill(
        source_path=source_path,
        skill_md_text="# dummy",
        content_hash=content_hash,
        last_commit_at=datetime.now(UTC),
    )
    analyzed = AnalyzedSkill(name=name, description=description, tags=["タグ"], usage="使い方")
    return AnalyzedResult(raw=raw, analyzed=analyzed, update_status=status, update_draft=draft)


def _make_repo(session: Session, repo: str) -> Repository:
    repository = Repository(owner="test-org", repo=repo)
    session.add(repository)
    session.flush()
    return repository


def test_persist_and_dedup_persists_and_detects_duplicate(
    db_session: Session, fake_embed_fn: Callable[[str], list[float]]
) -> None:
    repo = _make_repo(db_session, "dup-repo")
    results = [
        _result("skills/a/SKILL.md", "h1", "議事録要約", "会議の議事録を自動で要約し要点を箇条書きにする"),
        _result("skills/b/SKILL.md", "h2", "議事録要約", "会議の議事録を自動で要約し要点を箇条書きにする"),
    ]

    run_result = persist_and_dedup(db_session, repo.id, results, {"skipped": 3}, embed_fn=fake_embed_fn)

    stats = run_result.stats
    assert stats.collected == 2
    assert stats.skipped == 3
    assert stats.failed == 0
    assert stats.merge_suggestions == 1  # 似た 2 件 → merge 提案 1 件

    # seed や他テストと混ざらないよう、この repo に紐づくものだけ数える（生成件数は stats で検証済み）。
    assert db_session.scalar(select(func.count()).select_from(Skill).where(Skill.repo_id == repo.id)) == 2
    repo_embeddings = db_session.scalar(
        select(func.count())
        .select_from(SkillEmbedding)
        .join(Skill, Skill.id == SkillEmbedding.skill_id)
        .where(Skill.repo_id == repo.id)
    )
    assert repo_embeddings == 2


def test_persist_and_dedup_counts_needs_update(
    db_session: Session, fake_embed_fn: Callable[[str], list[float]]
) -> None:
    repo = _make_repo(db_session, "nu-repo")
    results = [
        _result(
            "skills/old/SKILL.md",
            "h1",
            "古い Skill",
            "古い API に依存している",
            status=UpdateStatus.NEEDS_UPDATE,
            draft="--- 修正方針の下書き ---",
        ),
    ]

    run_result = persist_and_dedup(db_session, repo.id, results, {}, embed_fn=fake_embed_fn)

    assert run_result.stats.needs_update == 1
    update_count = db_session.scalar(
        select(func.count()).select_from(Suggestion).where(Suggestion.type == SuggestionType.UPDATE)
    )
    assert update_count == 1


def test_persist_and_dedup_isolates_skill_failure(
    db_session: Session, fake_embed_fn: Callable[[str], list[float]]
) -> None:
    """1 Skill の埋め込み失敗が他 Skill を巻き込まない（savepoint でその Skill だけ巻き戻す）。"""
    repo = _make_repo(db_session, "err-repo")

    def flaky_embed(text: str) -> list[float]:
        if "壊れ" in text:
            raise RuntimeError("埋め込み失敗（テスト）")
        return fake_embed_fn(text)

    results = [
        _result("skills/ok/SKILL.md", "h1", "正常 Skill", "正常に処理できる説明"),
        _result("skills/bad/SKILL.md", "h2", "壊れ Skill", "壊れた埋め込みを誘発する"),
    ]

    run_result = persist_and_dedup(db_session, repo.id, results, {}, embed_fn=flaky_embed)

    assert run_result.stats.collected == 1
    assert run_result.stats.failed == 1

    names = set(db_session.scalars(select(Skill.name).where(Skill.repo_id == repo.id)).all())
    assert "正常 Skill" in names
    assert "壊れ Skill" not in names  # savepoint ロールバックで未保存
