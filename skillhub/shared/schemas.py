"""エージェントの構造化入出力に使う Pydantic スキーマ。

ADK の LlmAgent に `output_schema` として渡すことで、後段がパースしやすい
「契約」を固定する。DB のテーブル定義（DESIGN.md のER図）と対応させている。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FreshnessStatus = Literal["new", "stale", "needs_update"]
SuggestionType = Literal["merge", "improve", "compose", "update"]


# --- Collector が集めてくる生データ -----------------------------------------
class RawSkill(BaseModel):
    """1つの SKILL.md ディレクトリから収集した生の素材。"""

    repo_id: str
    source_path: str = Field(description="repo 内の SKILL.md パス")
    skill_md: str = Field(description="SKILL.md 本文")
    related_files: dict[str, str] = Field(
        default_factory=dict, description="同ディレクトリの関連ファイル {パス: 内容}"
    )
    author: str | None = None
    last_commit_at: str | None = None
    content_hash: str = Field(description="SKILL.md + 関連ファイルの SHA-256")


# --- Analyzer(Parser+Scorer) の構造化出力 -----------------------------------
class QualityBreakdown(BaseModel):
    description: int = Field(ge=0, le=100, description="説明の明確さ")
    trigger: int = Field(ge=0, le=100, description="トリガー精度")
    annotation: int = Field(ge=0, le=100, description="注釈の充実")


class AnalyzedSkill(BaseModel):
    """Parser と Scorer を1回のLLM呼び出しに統合した結果。"""

    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    usage: str = Field(description="自動生成した使い方の例")
    quality_breakdown: QualityBreakdown
    quality_score: int = Field(ge=0, le=100, description="加重平均の総合スコア")
    freshness_status: FreshnessStatus = "new"


# --- Deduper / Searcher / Composer / Improver -------------------------------
class SkillCandidate(BaseModel):
    skill_id: str
    name: str
    confidence: float = Field(ge=0.0, le=1.0)
    why: str = Field(description="推薦理由")


class SearchResult(BaseModel):
    candidates: list[SkillCandidate] = Field(default_factory=list)


class Suggestion(BaseModel):
    type: SuggestionType
    content: str
    target_skill_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    diff: str | None = Field(default=None, description="diff 下書き（任意）")
