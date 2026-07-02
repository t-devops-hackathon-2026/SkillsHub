"""サービス層（get_summary / collect_repo / Skill 詳細・提案レビュー）のテスト。

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
from skillshub.shared.agents import librarian as librarian_module
from skillshub.shared.agents.composer import ComposerWorkflow
from skillshub.shared.agents.librarian import LibrarianRunResult, LibrarianStats
from skillshub.shared.models import Repository, Skill, Suggestion, SuggestionTarget
from skillshub.shared.schemas import ComposeSuggestion, SuggestionStatus, SuggestionType, UpdateStatus
from skillshub.shared.tools import ai_tools


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


def test_collect_repo_runs_full_pipeline(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """collect_repo が run_librarian_for_repo を正しい引数で呼ぶことを検証する。"""
    repo = Repository(owner="o", repo="r")
    db_session.add(repo)
    db_session.flush()

    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))

    captured: dict[str, object] = {}

    def fake_run_librarian(
        repo_id: object,
        load_raw_skills: object,
        load_existing_hashes: object,
        *,
        embed_fn: object = None,
    ) -> LibrarianRunResult:
        captured["repo_id"] = repo_id
        return LibrarianRunResult(stats=LibrarianStats(collected=1, skipped=0))

    monkeypatch.setattr(librarian_module, "run_librarian_for_repo", fake_run_librarian)

    result = services.collect_repo(str(repo.id))

    assert result["collected_skills"] == 1
    assert result["skipped_skills"] == 0
    assert result["status"] == "success"
    assert captured["repo_id"] == repo.id


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


# ── Skill 詳細・提案レビュー（#20）──────────────────────────


def _add_skill(
    db_session: Session,
    repo: Repository,
    name: str,
    path: str,
    status: UpdateStatus = UpdateStatus.CURRENT,
) -> Skill:
    skill = Skill(repo_id=repo.id, name=name, description="d", source_path=path, update_status=status)
    db_session.add(skill)
    db_session.flush()
    return skill


def _add_suggestion(
    db_session: Session,
    type_: SuggestionType,
    targets: list[Skill],
    status: SuggestionStatus = SuggestionStatus.OPEN,
    content: str = "内容",
) -> Suggestion:
    suggestion = Suggestion(type=type_, content=content, status=status)
    db_session.add(suggestion)
    db_session.flush()
    db_session.add_all([SuggestionTarget(suggestion_id=suggestion.id, skill_id=s.id) for s in targets])
    db_session.flush()
    return suggestion


def test_get_skill_returns_detail_with_open_suggestions(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repository(owner="o", repo="r")
    db_session.add(repo)
    db_session.flush()
    skill = _add_skill(db_session, repo, "議事録要約", "skills/a/SKILL.md", UpdateStatus.NEEDS_UPDATE)
    other = _add_skill(db_session, repo, "タスク抽出", "skills/b/SKILL.md")
    open_sugg = _add_suggestion(db_session, SuggestionType.UPDATE, [skill], content="--- diff ---")
    # dismissed 済み・他 Skill 向けの提案は open_suggestions に含まれない。
    _add_suggestion(db_session, SuggestionType.MERGE, [skill], status=SuggestionStatus.DISMISSED)
    _add_suggestion(db_session, SuggestionType.UPDATE, [other])

    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))

    detail = services.get_skill(str(skill.id))
    assert detail is not None
    assert detail.skill.name == "議事録要約"
    assert detail.repo_owner == "o"
    assert detail.repo_name == "r"
    assert [s.id for s in detail.open_suggestions] == [open_sugg.id]
    assert detail.open_suggestions[0].targets[0].skill_name == "議事録要約"


def test_get_skill_returns_none_for_missing_id(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))
    assert services.get_skill("00000000-0000-0000-0000-000000000000") is None


def test_list_suggestions_returns_open_with_targets(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repository(owner="o", repo="r")
    db_session.add(repo)
    db_session.flush()
    s1 = _add_skill(db_session, repo, "議事録要約", "skills/a/SKILL.md")
    s2 = _add_skill(db_session, repo, "タスク抽出", "skills/b/SKILL.md")
    merge = _add_suggestion(db_session, SuggestionType.MERGE, [s1, s2])
    _add_suggestion(db_session, SuggestionType.UPDATE, [s1], status=SuggestionStatus.ACCEPTED)

    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))

    views = services.list_suggestions()
    assert [v.id for v in views] == [merge.id]
    assert {t.skill_name for t in views[0].targets} == {"議事録要約", "タスク抽出"}


def test_accept_update_suggestion_resets_update_status(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repository(owner="o", repo="r")
    db_session.add(repo)
    db_session.flush()
    skill = _add_skill(db_session, repo, "議事録要約", "skills/a/SKILL.md", UpdateStatus.NEEDS_UPDATE)
    suggestion = _add_suggestion(db_session, SuggestionType.UPDATE, [skill], content="--- diff ---")

    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))

    services.accept_suggestion(str(suggestion.id))

    assert suggestion.status == SuggestionStatus.ACCEPTED
    assert skill.update_status == UpdateStatus.CURRENT


def test_accept_merge_suggestion_keeps_skill_status(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repository(owner="o", repo="r")
    db_session.add(repo)
    db_session.flush()
    s1 = _add_skill(db_session, repo, "議事録要約", "skills/a/SKILL.md", UpdateStatus.STALE)
    s2 = _add_skill(db_session, repo, "タスク抽出", "skills/b/SKILL.md", UpdateStatus.STALE)
    suggestion = _add_suggestion(db_session, SuggestionType.MERGE, [s1, s2])

    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))

    services.accept_suggestion(str(suggestion.id))

    assert suggestion.status == SuggestionStatus.ACCEPTED
    # merge は記録のみ（GitHub への反映は作者が手元で行う）。Skill の状態は変えない。
    assert s1.update_status == UpdateStatus.STALE
    assert s2.update_status == UpdateStatus.STALE


def test_dismiss_suggestion_is_noop_when_not_open(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repository(owner="o", repo="r")
    db_session.add(repo)
    db_session.flush()
    skill = _add_skill(db_session, repo, "議事録要約", "skills/a/SKILL.md", UpdateStatus.NEEDS_UPDATE)
    suggestion = _add_suggestion(db_session, SuggestionType.UPDATE, [skill])

    monkeypatch.setattr(services, "_session_scope", lambda: _scope_yielding(db_session))

    services.dismiss_suggestion(str(suggestion.id))
    assert suggestion.status == SuggestionStatus.DISMISSED

    # 却下済みの提案を再度採用しても（再描画中の二度押し相当）何も変わらない。
    services.accept_suggestion(str(suggestion.id))
    assert suggestion.status == SuggestionStatus.DISMISSED
    assert skill.update_status == UpdateStatus.NEEDS_UPDATE

    with pytest.raises(ValueError, match="提案が見つかりません"):
        services.dismiss_suggestion("00000000-0000-0000-0000-000000000000")
