from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# ── Enums ──────────────────────────────────────────────


class UpdateStatus(enum.StrEnum):
    CURRENT = "current"
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


# ── Repository ─────────────────────────────────────────


class RepositoryBase(BaseModel):
    owner: str
    repo: str
    install_id: str | None = None


class Repository(RepositoryBase):
    id: UUID
    last_collected_at: datetime | None = None
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
    update_status: UpdateStatus = UpdateStatus.CURRENT
    content_hash: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Suggestion ─────────────────────────────────────────


class SuggestionBase(BaseModel):
    type: SuggestionType
    content: str


class Suggestion(SuggestionBase):
    id: UUID
    status: SuggestionStatus = SuggestionStatus.OPEN
    created_at: datetime
    target_skill_ids: list[UUID] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class SuggestionTargetRef(BaseModel):
    """提案が指す Skill への参照（画面でリンク・名前表示に使う）。"""

    skill_id: UUID
    skill_name: str


class SuggestionView(BaseModel):
    """提案レビュー画面・Skill 詳細画面で表示する 1 提案分のビュー。

    ``content`` は type=update のとき diff 下書き（er.md: diff は content に内包）。
    """

    id: UUID
    type: SuggestionType
    content: str
    status: SuggestionStatus
    created_at: datetime
    targets: list[SuggestionTargetRef] = Field(default_factory=list)


class SkillDetail(BaseModel):
    """Skill 詳細画面用。取得元リポジトリ情報と open な提案を同梱する。"""

    skill: Skill
    repo_owner: str
    repo_name: str
    open_suggestions: list[SuggestionView] = Field(default_factory=list)


# ── 司書バッチ: Collector → Analyzer 受け渡し ──────────────


class RawSkill(BaseModel):
    """Collector が後段（Analyzer）へ渡す 1 Skill の生データ。

    ADK の session state は JSON 可能な値しか載せられないため、``github_tools`` の
    ``CollectedSkill``（bytes を持つ dataclass）をテキスト化したものを受け渡し専用とする。
    """

    source_path: str
    skill_md_text: str
    related_file_names: list[str] = Field(default_factory=list)
    author: str | None = None
    last_commit_at: datetime | None = None
    content_hash: str


class AnalyzedSkill(BaseModel):
    """Analyzer（``output_schema``）が返す構造化解析結果。

    ``is_possibly_outdated`` は鮮度 ``needs_update`` 検知の兆候フラグ。Step1 には
    「既知のAPI変更」の突き合わせ先が無いため、LLM が SKILL.md 本文から
    deprecated / 古いバージョン参照などの兆候を見つけたら立てる。
    """

    name: str = Field(description="Skill の名前")
    description: str = Field(description="Skill が何をするかの簡潔な説明")
    tags: list[str] = Field(default_factory=list, description="分類タグ")
    usage: str = Field(description="使い方の要約")
    is_possibly_outdated: bool = Field(
        default=False,
        description="参照API・依存ツールに deprecated や古いバージョン参照などの兆候があれば true",
    )
    outdated_reason: str | None = Field(
        default=None,
        description="is_possibly_outdated が true のとき、その根拠（どの参照が古いか）",
    )


class UpdateDraft(BaseModel):
    """update 提案のドラフト（``output_schema``）。

    「こういう状況だから、こう直せば？」を管理者が数秒で読み取れるよう、
    状況・提案・diff を分けて生成させる。DB 保存時は 1 つのテキスト
    （suggestions.content）に整形して内包する（er.md: diff カラムは持たない）。
    """

    situation: str = Field(description="何がどう古いのか（1〜2文）")
    proposal: str = Field(description="どう直せばよいか（1〜2文、方向性を言い切る）")
    diff: str = Field(description="修正方針を示す unified diff（コードフェンスや説明文は含めない）")


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
