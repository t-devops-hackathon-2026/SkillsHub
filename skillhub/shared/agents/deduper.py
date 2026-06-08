"""DeduperAgent — ベクトル類似で重複候補を見つけ merge 提案を生成する。

pgvector 近傍検索（ツール）が主役で、LLM は merge 提案文の生成のみ。
similarity = 1 - cosine_distance。しきい値 0.88 以上を重複候補とする（環境変数化）。
ツールを使うため output_schema は付けず、生成した提案は db_tools 経由で保存する。
"""
from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from ..tools import db_tools
from ._model import FLASH_MODEL

deduper_agent = LlmAgent(
    name="DeduperAgent",
    model=FLASH_MODEL,
    instruction=(
        "保存した Skill の埋め込みで近傍検索を行い、類似度 0.88 以上（自分自身・同一"
        " source_path は除外）の組を重複候補とみなして merge 提案を作成・保存する。"
    ),
    tools=[
        FunctionTool(db_tools.search_similar),
        FunctionTool(db_tools.save_suggestion),
    ],
    output_key="dedup_result",
)
