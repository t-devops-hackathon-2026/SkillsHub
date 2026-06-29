"""SearcherAgent: 自然文クエリから候補 Skill を確信度・推薦理由つきで返す。

設計（docs/designs/step1/step1.md「自然言語検索」「ADKエージェント構成」）では Searcher は
「tools を持つ側（output_schema なし）」で、結果を ``output_key="search_result"`` に書く。

Deduper（agents/deduper.py）と同じ方針で、再現性とテスト容易性のため本体は決定論的な
純 Python（``run_searcher``）として実装する:

    クエリを Vertex AI で埋め込み → pgvector 近傍探索（top_k）→
    confidence = cosine 類似度（LLM に数値を出させない）→
    reason（why）だけ Gemini Flash で生成（仕様の Flash 使い分け。失敗時はテンプレ）

ADK の契約を満たす薄い ``LlmAgent`` ラッパ（``build_searcher_agent``）も用意し、将来
オンライン対話を ADK ``Runner`` に寄せる際の接続口にする。サービス層（shared.services）は
``run_searcher`` を直接呼ぶ。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from skillshub.shared.models import Skill
from skillshub.shared.schemas import SearchResultItem
from skillshub.shared.schemas import Skill as SkillSchema
from skillshub.shared.tools import ai_tools
from skillshub.shared.tools.ai_tools import EmbeddingFn

if TYPE_CHECKING:
    from google.adk.agents import LlmAgent

# クエリ・候補から推薦理由（why）を生成する関数型。既定は Gemini Flash だが、
# テストでは決定論的なフェイクを差し込めるよう注入可能にする。
ReasonFn = Callable[[str, list[Skill]], list[str]]

SEARCHER_MODEL = ai_tools.SEARCH_REASON_MODEL


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

    skills: list[Skill] = []
    similarities: list[float] = []
    for skill_id, similarity in candidates:
        skill = session.get(Skill, skill_id)
        if skill is None:  # 埋め込みは残っているが Skill が消えている等の不整合は飛ばす
            continue
        skills.append(skill)
        similarities.append(similarity)

    if not skills:
        return []

    reasons = _reasons_or_fallback(query, skills, similarities, reason)

    return [
        SearchResultItem(
            skill=SkillSchema.model_validate(skill),
            confidence=_clamp_confidence(similarity),
            reason=why,
        )
        for skill, similarity, why in zip(skills, similarities, reasons, strict=True)
    ]


def _reasons_or_fallback(
    query: str,
    skills: list[Skill],
    similarities: list[float],
    reason_fn: ReasonFn,
) -> list[str]:
    """推薦理由を生成する。LLM 失敗や件数不一致時はテンプレートにフォールバックする。"""
    try:
        reasons = reason_fn(query, skills)
    except Exception:  # noqa: BLE001 — LLM/GCP 失敗時も検索結果自体は返す（graceful degradation）
        reasons = []
    if len(reasons) != len(skills):
        return [
            _template_reason(query, skill, similarity) for skill, similarity in zip(skills, similarities, strict=True)
        ]
    return reasons


def _template_reason(query: str, skill: Skill, similarity: float) -> str:
    """LLM を使わない決定論的な推薦理由（フォールバック・テスト用）。"""
    return f"「{query}」との関連度は {similarity:.2f}。{skill.description}"


def _clamp_confidence(similarity: float) -> float:
    """cosine 類似度を確信度 [0.0, 1.0] に収める（負値は 0 に切り上げ）。"""
    return max(0.0, min(1.0, similarity))


def build_searcher_agent(model: str = SEARCHER_MODEL) -> LlmAgent:
    """ADK 契約用の薄い SearcherAgent を構築する（tools を持つ・output_schema なし）。

    オンライン対話を ADK ``Runner`` に寄せる際の接続口。実処理は ``run_searcher`` を
    直接呼ぶため、このエージェントは MVP では未使用。import は ADK 依存を遅延させる。
    """
    from google.adk.agents import LlmAgent
    from google.adk.tools.function_tool import FunctionTool

    return LlmAgent(
        name="searcher_agent",
        model=model,
        description="自然文クエリを埋め込み化し pgvector 近傍探索で候補 Skill を見つけ、確信度と推薦理由を付ける",
        instruction=(
            "ユーザーのクエリを埋め込み化し、近傍探索で類似 Skill を上位から取得してください。"
            "各候補に確信度（類似度）と推薦理由（why）を付けて search_result に書きます。"
        ),
        tools=[
            FunctionTool(ai_tools.embed_text),
            FunctionTool(ai_tools.search_similar_skills),
        ],
        output_key="search_result",
        # output_schema は設定しない（ADK 制約: tools と併用不可）。
    )
