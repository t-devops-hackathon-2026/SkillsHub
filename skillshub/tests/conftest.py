"""pytest 共有フィクスチャ。

DB を使うテストは ``DATABASE_URL`` が設定されていなければ自動スキップする
（ローカル/CI で GCP 認証なしに回せるよう、埋め込みは決定論的フェイクを注入する）。
DB を使う場合は alembic 適用済みのローカル pgvector（compose.yaml）を前提とし、
各テストはトランザクションを張って終了時にロールバックする。
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Callable, Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from skillshub.shared.models import EMBEDDING_DIM


def _fake_embed(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """文字トライグラムの正規化ベクトル。ほぼ同文なら高 cosine、別内容なら低 cosine。

    Vertex AI を呼ばずに決定論的なベクトルを返す（``hashlib`` でプロセス間も安定）。
    """
    vec = [0.0] * dim
    cleaned = text.strip()
    span = max(len(cleaned) - 2, 1)
    for i in range(span):
        trigram = cleaned[i : i + 3]
        bucket = int(hashlib.md5(trigram.encode("utf-8")).hexdigest(), 16) % dim
        vec[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


@pytest.fixture
def fake_embed_fn() -> Callable[[str], list[float]]:
    """決定論的なフェイク埋め込み関数（``EmbeddingFn`` 互換）。"""
    return _fake_embed


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """alembic 適用済みのローカル DB に接続し、テスト終了時にロールバックするセッション。"""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL 未設定のため DB テストをスキップ（compose の DB を起動して設定）")

    engine = create_engine(database_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
