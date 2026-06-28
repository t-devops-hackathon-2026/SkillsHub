"""DeduperAgent（重複検出・merge 提案生成）のテスト。

純ロジック（埋め込み入力組み立て）は DB 不要。重複検出本体は実 pgvector が要るため
``db_session`` fixture 経由（DATABASE_URL 未設定時は自動スキップ）。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skillshub.shared.agents.deduper import run_deduper_for_skill
from skillshub.shared.models import Repository, Skill, Suggestion, SuggestionTarget
from skillshub.shared.schemas import SuggestionType
from skillshub.shared.tools import ai_tools

# ── 純ロジック（DB 不要）──────────────────────────────────


def test_build_skill_embedding_input_joins_fields() -> None:
    skill = Skill(
        name="議事録要約",
        description="会議の議事録を要約する",
        source_path="skills/a/SKILL.md",
        usage="テキストを入れると要約します",
        tags=["議事録", "要約"],
    )
    text = ai_tools.build_skill_embedding_input(skill)
    assert "議事録要約" in text
    assert "会議の議事録を要約する" in text
    assert "テキストを入れると要約します" in text
    assert "議事録" in text and "要約" in text


def test_build_skill_embedding_input_skips_empty_optionals() -> None:
    skill = Skill(name="名前", description="説明", source_path="skills/b/SKILL.md", usage=None, tags=[])
    text = ai_tools.build_skill_embedding_input(skill)
    assert text == "名前\n説明"


# ── DB を使う統合テスト ──────────────────────────────────


def _make_repo(session: Session) -> Repository:
    repo = Repository(owner="test-org", repo="test-repo")
    session.add(repo)
    session.flush()
    return repo


def _make_skill(session: Session, repo: Repository, *, name: str, description: str, source_path: str) -> Skill:
    skill = Skill(repo_id=repo.id, name=name, description=description, source_path=source_path)
    session.add(skill)
    session.flush()
    return skill


def _count_merge_suggestions(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Suggestion).where(Suggestion.type == SuggestionType.MERGE))  # type: ignore[return-value]


def test_near_duplicate_creates_merge_suggestion(
    db_session: Session, fake_embed_fn: Callable[[str], list[float]]
) -> None:
    repo = _make_repo(db_session)
    skill_a = _make_skill(
        db_session,
        repo,
        name="議事録要約 Skill",
        description="会議の議事録を自動で要約し、要点を箇条書きにする",
        source_path="skills/meeting-summarizer-a/SKILL.md",
    )
    skill_b = _make_skill(
        db_session,
        repo,
        name="議事録要約 Skill",
        description="会議の議事録を自動で要約し、要点を箇条書きにする",
        source_path="skills/meeting-summarizer-b/SKILL.md",
    )

    # 先に a を処理（埋め込み登録のみ・候補なし）、次に b を処理して a を重複検出。
    assert run_deduper_for_skill(db_session, skill_a, embed_fn=fake_embed_fn) == []
    created = run_deduper_for_skill(db_session, skill_b, embed_fn=fake_embed_fn)

    assert len(created) == 1
    assert _count_merge_suggestions(db_session) == 1

    suggestion_id = created[0]
    target_ids = set(
        db_session.scalars(
            select(SuggestionTarget.skill_id).where(SuggestionTarget.suggestion_id == suggestion_id)
        ).all()
    )
    assert target_ids == {skill_a.id, skill_b.id}


def test_distinct_skills_no_suggestion(db_session: Session, fake_embed_fn: Callable[[str], list[float]]) -> None:
    repo = _make_repo(db_session)
    skill_a = _make_skill(
        db_session,
        repo,
        name="議事録要約",
        description="会議の議事録を要約して要点を箇条書きにする",
        source_path="skills/summarizer/SKILL.md",
    )
    skill_b = _make_skill(
        db_session,
        repo,
        name="画像リサイズ",
        description="アップロードされた画像を指定サイズへ変換する画像処理ユーティリティ",
        source_path="skills/image-resizer/SKILL.md",
    )

    run_deduper_for_skill(db_session, skill_a, embed_fn=fake_embed_fn)
    created = run_deduper_for_skill(db_session, skill_b, embed_fn=fake_embed_fn)

    assert created == []
    assert _count_merge_suggestions(db_session) == 0


def test_same_source_path_excluded(db_session: Session, fake_embed_fn: Callable[[str], list[float]]) -> None:
    repo = _make_repo(db_session)
    common_path = "skills/meeting-summarizer/SKILL.md"
    skill_a = _make_skill(
        db_session, repo, name="議事録要約 A", description="会議の議事録を要約する", source_path=common_path
    )
    skill_b = _make_skill(
        db_session, repo, name="議事録要約 B", description="会議の議事録を要約する", source_path=common_path
    )

    run_deduper_for_skill(db_session, skill_a, embed_fn=fake_embed_fn)
    created = run_deduper_for_skill(db_session, skill_b, embed_fn=fake_embed_fn)

    assert created == []
    assert _count_merge_suggestions(db_session) == 0


def test_idempotent_no_duplicate_suggestion(db_session: Session, fake_embed_fn: Callable[[str], list[float]]) -> None:
    repo = _make_repo(db_session)
    skill_a = _make_skill(
        db_session,
        repo,
        name="議事録要約 Skill",
        description="会議の議事録を自動で要約し、要点を箇条書きにする",
        source_path="skills/a/SKILL.md",
    )
    skill_b = _make_skill(
        db_session,
        repo,
        name="議事録要約 Skill",
        description="会議の議事録を自動で要約し、要点を箇条書きにする",
        source_path="skills/b/SKILL.md",
    )

    run_deduper_for_skill(db_session, skill_a, embed_fn=fake_embed_fn)
    first = run_deduper_for_skill(db_session, skill_b, embed_fn=fake_embed_fn)
    # 2 回目: 同一ペアは既存提案があるため新規生成しない。
    second = run_deduper_for_skill(db_session, skill_b, embed_fn=fake_embed_fn)

    assert len(first) == 1
    assert second == []
    assert _count_merge_suggestions(db_session) == 1
