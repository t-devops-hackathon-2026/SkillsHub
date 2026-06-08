"""ComposerAgent — 複数 Skill を組み合わせた合成ワークフローを提案する。

構造化出力（Suggestion: type="compose"）のため output_schema を付ける。
検索候補が2件以上のときに Searcher の後段として起動する想定（任意機能）。
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from ..schemas import Suggestion
from ._model import PRO_MODEL
from .prompts import texts

composer_agent = LlmAgent(
    name="ComposerAgent",
    model=PRO_MODEL,
    instruction=texts.COMPOSER,
    output_schema=Suggestion,
    output_key="compose_suggestion",
)
