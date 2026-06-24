from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# ── Enums ──────────────────────────────────────────────


class FreshnessStatus(enum.StrEnum):
    NEW = "new"
    STALE = "stale"
    NEEDS_UPDATE = "needs_update"


class SuggestionType(enum.StrEnum):
    MERGE = "merge"
    COMPOSE = "compose"
    UPDATE = "update"


class SuggestionStatus(enum.StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


class RepositoryStatus(enum.StrEnum):
    ACTIVE = "active"
    ERROR = "error"
    DISABLED = "disabled"


# ── Repository ─────────────────────────────────────────


class RepositoryBase(BaseModel):
    owner: str
    repo: str
    default_branch: str = "main"
    install_id: str | None = None


class Repository(RepositoryBase):
    id: UUID
    last_collected_at: datetime | None = None
    status: RepositoryStatus = RepositoryStatus.ACTIVE
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Skill ──────────────────────────────────────────────


class SkillBase(BaseModel):
    name: str
    description: str
    source_path: str
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    usage: str | None = None


class Skill(SkillBase):
    id: UUID
    repo_id: UUID
    last_updated: datetime | None = None
    freshness_status: FreshnessStatus = FreshnessStatus.NEW
    content_hash: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Suggestion ─────────────────────────────────────────


class SuggestionBase(BaseModel):
    type: SuggestionType
    content: str
    diff: dict | None = None  # type: ignore[type-arg]
    confidence: float | None = None


class Suggestion(SuggestionBase):
    id: UUID
    status: SuggestionStatus = SuggestionStatus.OPEN
    created_at: datetime
    target_skill_ids: list[UUID] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ── 検索結果 ───────────────────────────────────────────


class SearchResultItem(BaseModel):
    skill: Skill
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class ComposeSuggestion(BaseModel):
    title: str
    body: str
    target_skill_ids: list[UUID]


class SearchResult(BaseModel):
    items: list[SearchResultItem]
    compose_suggestion: ComposeSuggestion | None = None


# ── ダッシュボードサマリ ────────────────────────────────


class DashboardSummary(BaseModel):
    total_skills: int
    duplicate_candidates: int
    needs_update: int
    stale_count: int
