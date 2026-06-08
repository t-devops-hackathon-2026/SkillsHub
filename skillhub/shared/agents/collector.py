"""CollectorAgent — リポジトリを走査して SKILL.md 一式を取得する。

ツールを使う（GitHub アクセス）ので output_schema は付けない。
収集結果は後段（Analyzer）が state から参照する。
ADK 制約: output_schema を設定したエージェントは tools を使えないため役割を分離している。
"""
from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from ..tools import github_tools
from ._model import FLASH_MODEL

collector_agent = LlmAgent(
    name="CollectorAgent",
    model=FLASH_MODEL,
    instruction=(
        "登録リポジトリ内の SKILL.md ディレクトリを列挙し、各 Skill 一式を取得する。"
        "content_hash が前回と同一のものは変更なしとして次段に渡さない。"
    ),
    tools=[
        FunctionTool(github_tools.list_skill_dirs),
        FunctionTool(github_tools.fetch_skill),
    ],
    output_key="raw_skills",
)
