"""埋め込み生成と重複検出（pgvector 近傍探索）のツール群。

仕様の正は docs/designs/step1/step1.md「重複・類似検出」。

    SKILL.md 本文 → Vertex AI で 768 次元ベクトル化 → skill_embeddings に upsert
    → pgvector cosine 近傍探索（similarity = 1 - cosine_distance）
    → しきい値（既定 0.88, env DEDUP_THRESHOLD）以上を重複候補とし merge 提案を生成

副作用（DB 書き込み・外部 API 呼び出し）は各関数に閉じ込め、純粋な判定ロジックは
テストしやすいよう引数で差し替え可能にしている（``EmbeddingFn`` の注入）。

CLI 動作確認:

    python -m skillshub.shared.tools.ai_tools "ベクトル化したいテキスト"
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skillshub.shared.models import Skill, SkillEmbedding, Suggestion, SuggestionTarget
from skillshub.shared.schemas import SuggestionType

# ── 定数・型 ────────────────────────────────────────────

# 日英混在対応の多言語埋め込みモデル（768 次元）。
EMBEDDING_MODEL = "text-multilingual-embedding-002"

# テキスト → ベクトルの関数型。既定は Vertex AI だが、テストでは決定論的な
# フェイクを差し込めるように注入可能にしておく（dependency injection）。
EmbeddingFn = Callable[[str], list[float]]


# ── 埋め込み入力の組み立て ──────────────────────────────


def build_skill_embedding_input(skill: Skill) -> str:
    """Skill から埋め込み入力テキストを組み立てる（name / description / usage / tags）。

    SKILL.md 本文そのものは DB に持たないため、構造化済みの主要フィールドを連結して
    本文相当の意味ベクトルを得る。
    """
    parts = [skill.name, skill.description]
    if skill.usage:
        parts.append(skill.usage)
    if skill.tags:
        parts.append(" ".join(skill.tags))
    return "\n".join(p for p in parts if p)


# ── 埋め込み生成（Vertex AI）────────────────────────────


def embed_text(text: str, model: str = EMBEDDING_MODEL) -> list[float]:
    """Vertex AI でテキストを 768 次元ベクトルに変換する（既定の埋め込み実装）。

    ``google-cloud-aiplatform`` は関数内で遅延 import する。GCP 認証が無い環境
    （ローカルテスト等）では本関数を呼ばず ``EmbeddingFn`` のフェイクを注入する。
    """
    from vertexai.language_models import TextEmbeddingModel

    embedding_model = TextEmbeddingModel.from_pretrained(model)
    embeddings = embedding_model.get_embeddings([text])
    return list(embeddings[0].values)


# ── 埋め込みの永続化 ────────────────────────────────────


def upsert_skill_embedding(session: Session, skill_id: UUID, embedding: list[float]) -> None:
    """skill_embeddings に埋め込みを upsert する（skill_id 競合時は更新）。"""
    stmt = insert(SkillEmbedding).values(
        skill_id=skill_id,
        embedding=embedding,
        embedded_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SkillEmbedding.skill_id],
        set_={"embedding": stmt.excluded.embedding, "embedded_at": stmt.excluded.embedded_at},
    )
    session.execute(stmt)


# ── 近傍探索 ────────────────────────────────────────────


def find_similar_skills(
    session: Session,
    skill_id: UUID,
    embedding: list[float],
    source_path: str,
    *,
    threshold: float,
    limit: int = 5,
) -> list[tuple[UUID, float]]:
    """pgvector cosine 近傍探索で重複候補を返す。

    ``similarity = 1 - cosine_distance``。自分自身と同一 ``source_path``（同じファイル
    由来）は除外し、``similarity >= threshold`` のものだけを類似度降順で返す。
    """
    distance = SkillEmbedding.embedding.cosine_distance(embedding)
    similarity = (1 - distance).label("similarity")
    stmt = (
        select(SkillEmbedding.skill_id, similarity)
        .join(Skill, Skill.id == SkillEmbedding.skill_id)
        .where(SkillEmbedding.skill_id != skill_id)
        .where(Skill.source_path != source_path)
        .order_by(distance)
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    return [(row.skill_id, float(row.similarity)) for row in rows if row.similarity >= threshold]


# ── merge 提案の生成 ────────────────────────────────────


def _existing_open_merge(session: Session, skill_a_id: UUID, skill_b_id: UUID) -> UUID | None:
    """同一ペアの open な merge 提案が既にあればその id を返す（冪等性のため）。"""
    targets_a = select(SuggestionTarget.suggestion_id).where(SuggestionTarget.skill_id == skill_a_id)
    targets_b = select(SuggestionTarget.suggestion_id).where(SuggestionTarget.skill_id == skill_b_id)
    stmt = (
        select(Suggestion.id)
        .where(Suggestion.type == SuggestionType.MERGE)
        .where(Suggestion.status == "open")
        .where(Suggestion.id.in_(targets_a))
        .where(Suggestion.id.in_(targets_b))
        .limit(1)
    )
    return session.scalar(stmt)


def create_merge_suggestion(
    session: Session,
    skill_a: Skill,
    skill_b: Skill,
    similarity: float,
) -> UUID | None:
    """2 Skill の merge 提案（suggestion + suggestion_target 2行）を生成する。

    同一ペアの open な merge 提案が既にあれば何もせず ``None`` を返す（冪等）。
    """
    existing = _existing_open_merge(session, skill_a.id, skill_b.id)
    if existing is not None:
        return None

    content = (
        f"「{skill_a.name}」と「{skill_b.name}」は類似度 {similarity:.2f} で内容が重複している"
        "可能性があります。統合（merge）を検討してください。"
    )
    suggestion = Suggestion(type=SuggestionType.MERGE, content=content)
    session.add(suggestion)
    session.flush()  # id を採番してから target に紐付ける

    session.add_all(
        [
            SuggestionTarget(suggestion_id=suggestion.id, skill_id=skill_a.id),
            SuggestionTarget(suggestion_id=suggestion.id, skill_id=skill_b.id),
        ]
    )
    return suggestion.id


# ── CLI（動作確認用）────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print('usage: python -m skillshub.shared.tools.ai_tools "<text>"', file=sys.stderr)
        return 2
    vector = embed_text(args[0])
    print(f"dim={len(vector)} head={vector[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
