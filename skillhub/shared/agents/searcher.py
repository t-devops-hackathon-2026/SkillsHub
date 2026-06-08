"""SearcherAgent — 自然文クエリから候補 Skill を返す（RAG）。

クエリのベクトル化と pgvector 近傍検索（ツール）を行うため output_schema は付けない。
候補の整形・確信度・推薦理由は instruction で SearchResult 相当の形に寄せる。
"""
from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from ..tools import ai_tools, db_tools
from ._model import FLASH_MODEL
from .prompts import texts

searcher_agent = LlmAgent(
    name="SearcherAgent",
    model=FLASH_MODEL,
    instruction=texts.SEARCHER,
    tools=[
        FunctionTool(ai_tools.embed_text),
        FunctionTool(db_tools.search_similar),
    ],
    output_key="search_result",
)
