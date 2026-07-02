"""Deduper: Skill の重複を検出し merge 提案を生成する。

仕様の正は docs/designs/step1/step1.md「重複・類似検出」。重複判定の実体は
「cosine 類似度 >= しきい値なら merge 提案」という決定論的処理なので、LLM 判断は使わず
純 Python で実装する。バッチ（batch/run_dedup.py）と司書統括（agents/librarian.py）は
再現性のため ``run_deduper_for_skill`` を直接呼ぶ。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from skillshub.shared.config import get_dedup_threshold
from skillshub.shared.models import Skill
from skillshub.shared.tools import ai_tools
from skillshub.shared.tools.ai_tools import EmbeddingFn


def run_deduper_for_skill(
    session: Session,
    skill: Skill,
    *,
    embed_fn: EmbeddingFn | None = None,
    threshold: float | None = None,
) -> list[UUID]:
    """1 Skill について埋め込み生成→近傍探索→merge 提案生成までを行う（決定論的本体）。

    Args:
        session: アクティブな DB セッション（コミットは呼び出し側の責務）。
        skill: 対象の Skill（ORM）。
        embed_fn: テキスト→ベクトルの関数。未指定なら Vertex AI（``ai_tools.embed_text``）。
            テストでは決定論的なフェイクを注入する。
        threshold: 類似度しきい値。未指定なら ``get_dedup_threshold()``（既定 0.88）。

    Returns:
        新規に作成した merge 提案の id 一覧（既存と重複したペアは含まない）。
    """
    embed = embed_fn or ai_tools.embed_text
    limit = threshold if threshold is not None else get_dedup_threshold()

    text = ai_tools.build_skill_embedding_input(skill)
    embedding = embed(text)
    ai_tools.upsert_skill_embedding(session, skill.id, embedding)
    # 近傍探索が自分の埋め込みを参照できるよう、ここで flush しておく。
    session.flush()

    candidates = ai_tools.find_similar_skills(
        session,
        skill.id,
        embedding,
        skill.source_path,
        threshold=limit,
    )

    created: list[UUID] = []
    for candidate_id, similarity in candidates:
        candidate = session.get(Skill, candidate_id)
        if candidate is None:
            continue
        suggestion_id = ai_tools.create_merge_suggestion(session, skill, candidate, similarity)
        if suggestion_id is not None:
            created.append(suggestion_id)
    return created
