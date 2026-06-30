"""サービス層（get_summary / collect_repo）のテスト。

``_session_scope`` を db_session に差し替えて、実 DB（ローカル pgvector）に対する集計・
永続化を検証する（DATABASE_URL 未設定時は db_session fixture が自動スキップ）。
GitHub 収集は ``github_tools.collect_skills`` を monkeypatch してネットワークに触らない。
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from skillshub.shared import services
from skillshub.shared.agents import composer
from skillshub.shared.agents.composer import ComposerWorkflow
from skillshub.shared.models import Repository, Skill, Suggestion, SuggestionTarget
from skillshub.shared.schemas import ComposeSuggestion, SuggestionStatus, SuggestionType, UpdateStatus
from skillshub.shared.tools import ai_tools, github_tools


@contextmanager
def _scope_yielding(session: Session) -> Generator[Session, None, None]:
    """services._session_scope を差し替えるための CM。

    本物の _session_scope は正常終了時に commit（=flush）するので、テストでも exit 時に
    flush して書き込みを DB（テスト用トランザクション内）に反映する。commit はしない
    （conftest がトランザクションごと rollback して後始末するため）。
    """
    yield session
    session.flush()


def test_get_summary_counts(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repository(owner="o", repo="r")
    db_session.add(repo)
    db_session.flush()

    def _add(name: str, status: UpdateStatus, path: str) -> None:
        db_session.add(Skill(repo_id=repo.id, name=name, description="d", source_path=path, update_status=status))

    _add("a", UpdateStatus.CURRENT, "skills/a/SKILL.md")
    _add("b", UpdateStatus.STALE, "skills/b/SKILL.md")
    _add("c", UpdateStatus.STALE, "skills/c/SKILL.md")
    _add("d", UpdateStatus.NEEDS_UPDATE, "skills/d/SKILL.md")
    # open な merge 提案だけが重複候補に数えられる（dismissed は対象外）。
    db_session.add(Suggestion(type=SuggestionType.MERGE, content="x", status=SuggestionStatus.OPEN))
    db_session.add(Suggestion(type=SuggestionType.MERGE, content="y", status=SuggestionStatus.DISMISSED))
    db_session.flush()

    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))

    summary = services.get_summary()
    assert summary.total_skills == 4
    assert summary.stale_count == 2
    assert summary.needs_update == 1
    assert summary.duplicate_candidates == 1


def _collected(skill_md_text: str, *, path: str = "skills/foo/SKILL.md") -> github_tools.CollectedSkill:
    return github_tools.CollectedSkill(
        owner="o",
        repo="r",
        skill_dir="skills/foo",
        skill_md_path=path,
        skill_md=github_tools.SkillFile(path=path, content=skill_md_text.encode("utf-8")),
        related_files=[],
        author="alice",
        last_commit_at=None,
        content_hash="hash1",
    )


def test_collect_repo_inserts_then_updates(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repository(owner="o", repo="r")
    db_session.add(repo)
    db_session.flush()

    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))

    md = "---\nname: 議事録要約\ndescription: 会議の議事録を要約する\n---\n# 本文\n"
    monkeypatch.setattr(github_tools, "collect_skills", lambda target: [_collected(md)])

    result = services.collect_repo(str(repo.id))
    assert result["collected_skills"] == 1
    assert result["new_skills"] == 1
    assert result["updated_skills"] == 0

    skill = db_session.scalar(select(Skill).where(Skill.source_path == "skills/foo/SKILL.md"))
    assert skill is not None
    assert skill.name == "議事録要約"
    assert skill.description == "会議の議事録を要約する"
    assert skill.content_hash == "hash1"

    # 2 回目: 同一 source_path は更新（new ではなく updated）。
    md2 = "---\nname: 議事録要約v2\ndescription: 更新後の説明\n---\n"
    monkeypatch.setattr(github_tools, "collect_skills", lambda target: [_collected(md2)])

    result2 = services.collect_repo(str(repo.id))
    assert result2["new_skills"] == 0
    assert result2["updated_skills"] == 1

    db_session.expire_all()
    skill2 = db_session.scalar(select(Skill).where(Skill.source_path == "skills/foo/SKILL.md"))
    assert skill2 is not None
    assert skill2.name == "議事録要約v2"
    assert skill2.description == "更新後の説明"


def test_search_skills_end_to_end(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_embed_fn: Callable[[str], list[float]],
) -> None:
    """search_skills の配線（候補2件＋合成提案）を GCP 非依存で end-to-end 検証する。

    本番は埋め込み=Vertex / reason=Gemini Flash / compose=Gemini Pro だが、ここでは
    それぞれフェイクへ差し替えて、pgvector 近傍検索→Composer 起動の分岐までを通す。
    """
    repo = Repository(owner="o", repo="r")
    db_session.add(repo)
    db_session.flush()

    def _add(name: str, description: str, path: str) -> None:
        skill = Skill(repo_id=repo.id, name=name, description=description, source_path=path)
        db_session.add(skill)
        db_session.flush()
        embedding = fake_embed_fn(ai_tools.build_skill_embedding_input(skill))
        ai_tools.upsert_skill_embedding(db_session, skill.id, embedding)
        db_session.flush()

    _add("議事録要約", "会議の議事録を要約して要点を箇条書きにする", "skills/minutes/SKILL.md")
    _add("タスク抽出", "議事録やメモからアクションアイテムを抽出する", "skills/tasks/SKILL.md")

    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))
    monkeypatch.setattr(ai_tools, "embed_text", fake_embed_fn)
    monkeypatch.setattr(
        ai_tools,
        "generate_search_reasons",
        lambda query, skills: [f"理由:{s.name}" for s in skills],
    )
    monkeypatch.setattr(
        composer,
        "_generate_compose",
        lambda query, items, model: ComposerWorkflow(title="議事録→タスク ワークフロー", body="要約後にタスク抽出"),
    )

    result = services.search_skills("議事録 要約")

    assert {item.skill.name for item in result.items} == {"議事録要約", "タスク抽出"}
    assert all(0.0 <= item.confidence <= 1.0 for item in result.items)
    assert all(item.reason.startswith("理由:") for item in result.items)
    assert result.compose_suggestion is not None
    assert result.compose_suggestion.title == "議事録→タスク ワークフロー"
    assert len(result.compose_suggestion.target_skill_ids) == 2


def test_register_compose_suggestion_persists(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repository(owner="o", repo="r")
    db_session.add(repo)
    db_session.flush()
    s1 = Skill(repo_id=repo.id, name="議事録要約", description="d", source_path="skills/a/SKILL.md")
    s2 = Skill(repo_id=repo.id, name="タスク抽出", description="d", source_path="skills/b/SKILL.md")
    db_session.add_all([s1, s2])
    db_session.flush()

    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))

    compose = ComposeSuggestion(title="統合WF", body="要約してからタスク抽出", target_skill_ids=[s1.id, s2.id])
    suggestion_id = services.register_compose_suggestion(compose)

    saved = db_session.get(Suggestion, suggestion_id)
    assert saved is not None
    assert saved.type == SuggestionType.COMPOSE
    assert "統合WF" in saved.content
    targets = set(
        db_session.scalars(
            select(SuggestionTarget.skill_id).where(SuggestionTarget.suggestion_id == suggestion_id)
        ).all()
    )
    assert targets == {s1.id, s2.id}
