"""AnalyzerAgent — Parser と Scorer を統合した解析エージェント。

SKILL.md の構造化（name/description/tags/usage）と品質採点・鮮度仮判定を
1回のLLM呼び出しで行い、呼び出し回数とコストを抑える（DESIGN.md の統合方針）。
構造化出力のため output_schema を付ける（→ このエージェントは tools を持てない）。
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..schemas import AnalyzedSkill
from ._model import FLASH_MODEL
from .prompts import texts

analyzer_agent = LlmAgent(
    name="AnalyzerAgent",
    model=FLASH_MODEL,
    instruction=texts.ANALYZER,
    output_schema=AnalyzedSkill,
    output_key="analyzed_skill",
)
