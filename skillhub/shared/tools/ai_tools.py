"""Vertex AI 埋め込み生成ツール（雛形）。

text-multilingual-embedding-002（768次元）で本文・クエリをベクトル化する。
"""
from __future__ import annotations

EMBEDDING_DIM = 768


def embed_text(text: str) -> list[float]:
    """テキストを 768 次元ベクトルに変換する。

    Skill 本文の埋め込み（収集時）と検索クエリの埋め込み（オンライン）で共用。
    """
    # TODO: google-genai / Vertex AI Embeddings で実装
    raise NotImplementedError
