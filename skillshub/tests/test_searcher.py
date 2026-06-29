"""SearcherAgent（自然文検索）のテスト。

純ロジック（確信度のクランプ）は DB 不要。検索本体は実 pgvector が要るため
``db_session`` fixture 経由（DATABASE_URL 未設定時は自動スキップ）。埋め込みと推薦理由は
決定論的なフェイクを注入し、GCP に触らずに回せるようにする。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from skillshub.shared.agents import searcher
from skillshub.shared.agents.searcher import run_searcher
from skillshub.shared.models import Repository, Skill
from skillshub.shared.tools import ai_tools

# ── 純ロジック（DB 不要）──────────────────────────────────


def test_clamp_confidence_bounds() -> None:
    assert searcher._clamp_confidence(1.5) == 1.0
    assert searcher._clamp_confidence(-0.3) == 0.0
    assert searcher._clamp_confidence(0.42) == 0.42


# ── DB を使う統合テスト ──────────────────────────────────


def _make_repo(session: Session) -> Repository:
    repo = Repository(owner="test-org", repo="test-repo")
    session.add(repo)
    session.flush()
    return repo


def _add_skill_with_embedding(
    session: Session,
    repo: Repository,
    *,
    name: str,
    description: str,
    source_path: str,
    embed_fn: Callable[[str], list[float]],
) -> Skill:
    skill = Skill(repo_id=repo.id, name=name, description=description, source_path=source_path)
    session.add(skill)
    session.flush()
    text = ai_tools.build_skill_embedding_input(skill)
    ai_tools.upsert_skill_embedding(session, skill.id, embed_fn(text))
    session.flush()
    return skill


def test_run_searcher_ranks_relevant_skill_first(
    db_session: Session, fake_embed_fn: Callable[[str], list[float]]
) -> None:
    repo = _make_repo(db_session)
    _add_skill_with_embedding(
        db_session,
        repo,
        name="議事録要約",
        description="会議の議事録を要約して要点を箇条書きにする",
        source_path="skills/minutes/SKILL.md",
        embed_fn=fake_embed_fn,
    )
    _add_skill_with_embedding(
        db_session,
        repo,
        name="画像リサイズ",
        description="アップロードされた画像を指定サイズへ変換する画像処理ユーティリティ",
        source_path="skills/image/SKILL.md",
        embed_fn=fake_embed_fn,
    )

    def fake_reason(query: str, skills: list[Skill]) -> list[str]:
        return [f"why:{s.name}" for s in skills]

    items = run_searcher(
        db_session,
        "議事録を要約したい",
        embed_fn=fake_embed_fn,
        reason_fn=fake_reason,
        top_k=3,
    )

    names = [item.skill.name for item in items]
    assert "議事録要約" in names
    # 関連の高い議事録要約が、無関係な画像リサイズより上位に来る。
    assert names.index("議事録要約") < names.index("画像リサイズ")
    # 確信度は [0,1] に収まり、推薦理由は reason_fn の出力が使われる。
    assert all(0.0 <= item.confidence <= 1.0 for item in items)
    top = items[0]
    assert top.reason == f"why:{top.skill.name}"


def test_run_searcher_respects_top_k(db_session: Session, fake_embed_fn: Callable[[str], list[float]]) -> None:
    repo = _make_repo(db_session)
    for i in range(3):
        _add_skill_with_embedding(
            db_session,
            repo,
            name=f"Skill {i}",
            description=f"説明テキスト {i}",
            source_path=f"skills/s{i}/SKILL.md",
            embed_fn=fake_embed_fn,
        )

    items = run_searcher(
        db_session,
        "説明テキスト",
        embed_fn=fake_embed_fn,
        reason_fn=lambda q, s: ["r"] * len(s),
        top_k=1,
    )
    assert len(items) == 1


def test_run_searcher_falls_back_to_template_on_reason_error(
    db_session: Session, fake_embed_fn: Callable[[str], list[float]]
) -> None:
    repo = _make_repo(db_session)
    _add_skill_with_embedding(
        db_session,
        repo,
        name="議事録要約",
        description="会議の議事録を要約する",
        source_path="skills/minutes/SKILL.md",
        embed_fn=fake_embed_fn,
    )

    def boom(query: str, skills: list[Skill]) -> list[str]:
        raise RuntimeError("LLM down")

    items = run_searcher(db_session, "議事録", embed_fn=fake_embed_fn, reason_fn=boom, top_k=3)
    assert len(items) == 1
    # LLM 失敗時はテンプレート（関連度つき）にフォールバックする。
    assert "関連度" in items[0].reason
