"""SQLAlchemy ORM モデル（er.md 確定版に準拠）。

カラム定義の正は docs/designs/step1/er.md。テーブル名は複数形、FK カラムは単数。
enum 相当の列は VARCHAR + CHECK 制約で表現する（アプリ側は schemas.py の StrEnum で検証）。
Step1 では usage_events / skills.quality_score などは作らない（Step3）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 768


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        # 同一リポジトリの二重登録を防ぐ
        Index("ux_repositories_owner_repo", "owner", "repo", unique=True),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    owner: Mapped[str] = mapped_column(String, nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    install_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    skills: Mapped[list[Skill]] = relationship(back_populates="repository", cascade="all, delete-orphan")


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(
            "update_status IN ('current', 'stale', 'needs_update')",
            name="ck_skills_update_status",
        ),
        Index("ix_skills_update_status", "update_status"),
        Index("ix_skills_updated_at", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    repo_id: Mapped[UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_path: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    usage: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    update_status: Mapped[str] = mapped_column(String, nullable=False, server_default="current")
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    repository: Mapped[Repository] = relationship(back_populates="skills")
    embedding: Mapped[SkillEmbedding | None] = relationship(
        back_populates="skill", cascade="all, delete-orphan", uselist=False
    )


class SkillEmbedding(Base):
    __tablename__ = "skill_embeddings"
    __table_args__ = (
        # 件数が少なく学習不要な hnsw を採用（cosine 距離）
        Index(
            "ix_skill_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    # 1 Skill に 0/1 の埋め込み。skill_id が PK 兼 FK。
    skill_id: Mapped[UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    embedded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    skill: Mapped[Skill] = relationship(back_populates="embedding")


class Suggestion(Base):
    __tablename__ = "suggestions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('merge', 'compose', 'update')",
            name="ck_suggestions_type",
        ),
        CheckConstraint(
            "status IN ('open', 'accepted', 'dismissed')",
            name="ck_suggestions_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    targets: Mapped[list[SuggestionTarget]] = relationship(back_populates="suggestion", cascade="all, delete-orphan")


class SuggestionTarget(Base):
    """suggestion × skill の多対多ブリッジ（compose が複数 Skill を参照するため）。"""

    __tablename__ = "suggestion_targets"

    suggestion_id: Mapped[UUID] = mapped_column(ForeignKey("suggestions.id", ondelete="CASCADE"), primary_key=True)
    skill_id: Mapped[UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)

    suggestion: Mapped[Suggestion] = relationship(back_populates="targets")
