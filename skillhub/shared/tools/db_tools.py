"""Cloud SQL(PostgreSQL + pgvector) アクセスツール群（雛形）。"""
from __future__ import annotations


def upsert_skill(skill: dict) -> str:
    """解析済み Skill を保存（差分は content_hash で判定）。Returns: skill_id。"""
    # TODO: db.py の接続を使って UPSERT
    raise NotImplementedError


def save_embedding(skill_id: str, embedding: list[float]) -> None:
    """SKILL_EMBEDDING に vector(768) を保存する。"""
    # TODO: pgvector へ INSERT/UPDATE
    raise NotImplementedError


def search_similar(embedding: list[float], top_k: int = 5) -> list[dict]:
    """pgvector cosine 近傍検索で類似 Skill を返す。

    similarity = 1 - cosine_distance。Deduper / Searcher が共用する。
    """
    # TODO: ORDER BY embedding <=> :query LIMIT :top_k
    raise NotImplementedError


def save_suggestion(suggestion: dict) -> str:
    """SUGGESTION + SUGGESTION_TARGET を保存する。Returns: suggestion_id。"""
    # TODO: ブリッジ表 suggestion_target も同時に INSERT
    raise NotImplementedError
