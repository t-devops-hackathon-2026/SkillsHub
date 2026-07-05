"""埋め込み生成と重複検出（pgvector 近傍探索）のツール群。

仕様の正は docs/designs/step1/step1.md「重複・類似検出」。

    SKILL.md 本文 → Gemini 埋め込みモデルで 768 次元ベクトル化 → skill_embeddings に upsert
    → pgvector cosine 近傍探索（similarity = 1 - cosine_distance）
    → しきい値（既定 0.88, env DEDUP_THRESHOLD）以上を重複候補とし merge 提案を生成

副作用（DB 書き込み・外部 API 呼び出し）は各関数に閉じ込め、純粋な判定ロジックは
テストしやすいよう引数で差し替え可能にしている（``EmbeddingFn`` の注入）。

CLI 動作確認:

    python -m skillshub.shared.tools.ai_tools "ベクトル化したいテキスト"
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skillshub.shared.models import Skill, SkillEmbedding, Suggestion, SuggestionTarget
from skillshub.shared.schemas import ComposeSuggestion, SuggestionType

# ── 定数・型 ────────────────────────────────────────────

# 日英混在対応の多言語埋め込みモデル。既定次元は 3072 だが、DB スキーマ（vector(768)）に
# 合わせて output_dimensionality=768 で切り詰めて使う（embed_text 参照）。
EMBEDDING_MODEL = "gemini-embedding-001"

# DB の skill_embeddings.embedding（vector(768)）に合わせた埋め込み次元数。
EMBEDDING_DIM = 768

# 検索の推薦理由（why）生成に使う既定モデル。仕様の「Flash 既定 / 重い推論のみ Pro」に従い Flash。
SEARCH_REASON_MODEL = "gemini-3-flash"

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


# ── 埋め込み生成（Gemini API / Vertex AI）───────────────


@lru_cache(maxsize=1)
def get_genai_client():  # type: ignore[no-untyped-def]
    """google-genai クライアントを生成する（プロセス内で 1 個を使い回す）。

    認証・接続先は環境変数で決まる: ``GOOGLE_API_KEY``（Gemini API 直、ローカル向け）
    または ``GOOGLE_GENAI_USE_VERTEXAI=TRUE`` ＋ ``GOOGLE_CLOUD_PROJECT`` ＋
    ``GOOGLE_CLOUD_LOCATION``（Vertex AI 経由・ADC 認証、デプロイ環境向け）。
    """
    from google import genai

    return genai.Client()


def embed_text(text: str, model: str = EMBEDDING_MODEL) -> list[float]:
    """Gemini 埋め込みモデルでテキストを 768 次元ベクトルに変換する（既定の埋め込み実装）。

    ``google-genai`` は関数内で遅延 import する。Gemini 認証が無い環境
    （ローカルテスト等）では本関数を呼ばず ``EmbeddingFn`` のフェイクを注入する。

    gemini-embedding-001 は 3072 未満に切り詰めたベクトルを正規化しないため、
    cosine 類似度の前提を揃えるよう単位ベクトル化してから返す。
    """
    from google.genai import types

    result = get_genai_client().models.embed_content(
        model=model,
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
    )
    values = list(result.embeddings[0].values)
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0.0:
        return values
    return [v / norm for v in values]


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


# ── 検索（オンライン: クエリ→候補）──────────────────────


def search_similar_skills(
    session: Session,
    query_embedding: list[float],
    *,
    top_k: int = 3,
    threshold: float = 0.0,
) -> list[tuple[UUID, float]]:
    """クエリ埋め込みに対する近傍 Skill を similarity 降順で最大 top_k 件返す（意味検索）。

    重複検出用の ``find_similar_skills`` と異なり、自分自身・同一 ``source_path`` の
    除外はしない（検索は全 Skill が対象）。``similarity = 1 - cosine_distance``。
    ``threshold`` 未満は落とす（既定 0.0 = フィルタなし。負の類似度のみ除外したい等で利用）。
    """
    distance = SkillEmbedding.embedding.cosine_distance(query_embedding)
    similarity = (1 - distance).label("similarity")
    stmt = select(SkillEmbedding.skill_id, similarity).order_by(distance).limit(top_k)
    rows = session.execute(stmt).all()
    return [(row.skill_id, float(row.similarity)) for row in rows if row.similarity >= threshold]


def generate_search_reasons(query: str, skills: list[Skill], model: str = SEARCH_REASON_MODEL) -> list[str]:
    """各候補 Skill について、クエリに対する推薦理由（why）を Gemini Flash で生成する。

    候補をまとめて 1 回の呼び出しで処理し、入力と同順・同数の理由リストを返す。
    ``google-genai`` は関数内で遅延 import する（Gemini 認証が無い環境では本関数を呼ばない）。
    呼び出し側（``run_searcher``）は本関数が失敗した場合テンプレートにフォールバックする。
    """
    from google.genai import types

    numbered = "\n".join(f"{i}. 名前: {s.name} / 説明: {s.description}" for i, s in enumerate(skills))
    prompt = (
        "あなたは社内 Skill 検索の推薦理由を書くアシスタントです。\n"
        f"ユーザーのやりたいこと（クエリ）: 「{query}」\n\n"
        "次の各候補について、なぜこのクエリに合致するのかを日本語1〜2文で簡潔に説明してください。\n"
        f"{numbered}\n\n"
        '出力は {"reasons": ["理由0", "理由1", ...]} の JSON のみ。配列は候補と同じ順序・同じ件数にすること。'
    )
    schema = {
        "type": "object",
        "properties": {"reasons": {"type": "array", "items": {"type": "string"}}},
        "required": ["reasons"],
    }
    response = get_genai_client().models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.2,
        ),
    )
    data = json.loads(response.text)
    reasons = data.get("reasons")
    if not isinstance(reasons, list):
        raise ValueError("Gemini の応答に reasons 配列が含まれていません")
    return [str(r) for r in reasons]


# ── merge 提案の生成 ────────────────────────────────────


def _existing_merge_for_pair(session: Session, skill_a_id: UUID, skill_b_id: UUID) -> UUID | None:
    """同一ペアの merge 提案が既にあればその id を返す（status は問わない）。

    open だけでなく accepted / dismissed も対象にする。一度人間が判断したペアに
    次の収集で同じ提案を作り直すと、判断（特に「対応しない」）が無視されてしまうため。
    """
    targets_a = select(SuggestionTarget.suggestion_id).where(SuggestionTarget.skill_id == skill_a_id)
    targets_b = select(SuggestionTarget.suggestion_id).where(SuggestionTarget.skill_id == skill_b_id)
    stmt = (
        select(Suggestion.id)
        .where(Suggestion.type == SuggestionType.MERGE)
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

    同一ペアの merge 提案が既にあれば（判断済みも含め）何もせず ``None`` を返す（冪等）。
    """
    existing = _existing_merge_for_pair(session, skill_a.id, skill_b.id)
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


# ── compose 提案の生成 ──────────────────────────────────


def create_compose_suggestion(session: Session, compose: ComposeSuggestion) -> UUID:
    """合成提案（suggestion + suggestion_target N 行）を保存し、新規 id を返す。

    merge と異なり対象 Skill は可変個（候補全件）なので targets を複数作る。検索画面（#19）の
    「採用」操作から呼ぶ想定で、``search_skills`` は提案を返すだけで保存しない（毎回の検索で
    提案が増えないよう、保存はユーザーの採用操作に限定する）。
    """
    content = f"{compose.title}\n\n{compose.body}"
    suggestion = Suggestion(type=SuggestionType.COMPOSE, content=content)
    session.add(suggestion)
    session.flush()  # id を採番してから target に紐付ける

    session.add_all(
        [SuggestionTarget(suggestion_id=suggestion.id, skill_id=skill_id) for skill_id in compose.target_skill_ids]
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
