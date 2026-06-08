"""ImproverAgent — Skill の改善 diff を提案する。

構造化出力（Suggestion: type="improve"）のため output_schema を付ける。
詳細画面から対象 Skill を指定して起動する想定（任意機能）。
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..schemas import Suggestion
from ._model import PRO_MODEL
from .prompts import texts

improver_agent = LlmAgent(
    name="ImproverAgent",
    model=PRO_MODEL,
    instruction=texts.IMPROVER,
    output_schema=Suggestion,
    output_key="improve_suggestion",
)
