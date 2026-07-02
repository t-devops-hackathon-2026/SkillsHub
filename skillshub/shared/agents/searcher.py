"""Searcher: 自然文クエリから候補 Skill を確信度・推薦理由つきで返す。

仕様の正は docs/designs/step1/step1.md「自然言語検索」。再現性とテスト容易性のため、
本体は決定論的な純 Python（``run_searcher``）として実装する:

    クエリを Vertex AI で埋め込み → pgvector 近傍探索（top_k）→
    confidence = cosine 類似度（LLM に数値を出させない）→
    reason（why）だけ Gemini Flash で生成（失敗時はテンプレートにフォールバック）

サービス層（shared.services.search_skills）が ``run_searcher`` を直接呼ぶ。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from skillshub.shared.models import Skill
from skillshub.shared.schemas import SearchResultItem
from skillshub.shared.schemas import Skill as SkillSchema
from skillshub.shared.tools import ai_tools
from skillshub.shared.tools.ai_tools import EmbeddingFn

# クエリ・候補から推薦理由（why）を生成する関数型。既定は Gemini Flash だが、
# テストでは決定論的なフェイクを差し込めるよう注入可能にする。
ReasonFn = Callable[[str, list[Skill]], list[str]]


def run_searcher(
    session: Session,
    query: str,
    *,
    embed_fn: EmbeddingFn | None = None,
    reason_fn: ReasonFn | None = None,
    top_k: int = 3,
) -> list[SearchResultItem]:
    """自然文クエリに対する候補 Skill を確信度・推薦理由つきで返す（決定論的本体）。

    Args:
        session: アクティブな DB セッション（読み取りのみ。コミット不要）。
        query: ユーザーの「やりたいこと」自然文。
        embed_fn: テキスト→ベクトルの関数。未指定なら Vertex AI（``ai_tools.embed_text``）。
        reason_fn: 推薦理由の生成関数。未指定なら Gemini Flash（``ai_tools.generate_search_reasons``）。
        top_k: 近傍探索で取得する候補上限（仕様の既定は 3）。

    Returns:
        確信度降順（近傍探索順）に並んだ候補リスト。候補なしなら空リスト。
    """
    embed = embed_fn or ai_tools.embed_text
    reason = reason_fn or ai_tools.generate_search_reasons

    query_embedding = embed(query)
    candidates = ai_tools.search_similar_skills(session, query_embedding, top_k=top_k)

    # (Skill, 類似度) に解決する。埋め込みだけ残って Skill が消えている等の不整合は飛ばす。
    matches = [
        (skill, similarity)
        for skill_id, similarity in candidates
        if (skill := session.get(Skill, skill_id)) is not None
    ]
    if not matches:
        return []

    reasons = _reasons_or_fallback(query, matches, reason)

    return [
        SearchResultItem(
            skill=SkillSchema.model_validate(skill),
            confidence=_clamp_confidence(similarity),
            reason=why,
        )
        for (skill, similarity), why in zip(matches, reasons, strict=True)
    ]


def _reasons_or_fallback(
    query: str,
    matches: list[tuple[Skill, float]],
    reason_fn: ReasonFn,
) -> list[str]:
    """推薦理由を生成する。LLM 失敗や件数不一致時はテンプレートにフォールバックする。"""
    try:
        reasons = reason_fn(query, [skill for skill, _ in matches])
    except Exception:  # noqa: BLE001 — LLM/GCP 失敗時も検索結果自体は返す（graceful degradation）
        reasons = []
    if len(reasons) != len(matches):
        return [_template_reason(query, skill, similarity) for skill, similarity in matches]
    return reasons


def _template_reason(query: str, skill: Skill, similarity: float) -> str:
    """LLM を使わない決定論的な推薦理由（フォールバック・テスト用）。"""
    return f"「{query}」との関連度は {similarity:.2f}。{skill.description}"


def _clamp_confidence(similarity: float) -> float:
    """cosine 類似度を確信度 [0.0, 1.0] に収める（負値は 0 に切り上げ）。"""
    return max(0.0, min(1.0, similarity))
