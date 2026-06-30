"""CollectorAgent: 司書パイプライン 1 段目（収集係）。

役割分担の方針（#7 結論）に従い、Collector は「tools 組（構造化出力なし）」。ただし収集
ロジック（走査・取得・hash 計算）は ``github_tools`` / ``sources.local`` に確定的な関数として
完成しているため、それを LLM に判断させる意味は無い。よって Collector は ADK の custom
``BaseAgent`` として確定的に動き、変更分だけを ``output_key`` 経由で後段へ渡す。

差分検知: 取得した ``content_hash`` を DB の既存値と比較し、変わった/新規のものだけ通す。
未変更はスキップして LLM（Analyzer）のコスト・レート消費を防ぐ（収集の要）。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import override

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from skillshub.shared.schemas import RawSkill

DEFAULT_OUTPUT_KEY = "raw_skills"


def select_changed(raw_skills: list[RawSkill], existing_hashes: dict[str, str]) -> list[RawSkill]:
    """既存の ``content_hash`` と比較し、変更分（新規・更新）だけを返す純関数。

    ``existing_hashes`` は ``{source_path: content_hash}``。同一 hash はスキップする。
    """
    return [s for s in raw_skills if existing_hashes.get(s.source_path) != s.content_hash]


class CollectorAgent(BaseAgent):
    """収集源から ``RawSkill`` を集め、変更分のみ session state に書き出す BaseAgent。

    ``load_raw_skills`` … 収集源（ローカル / GitHub）から全 ``RawSkill`` を返す。
    ``load_existing_hashes`` … DB から既存 ``{source_path: content_hash}`` を返す（未接続なら空）。
    """

    load_raw_skills: Callable[[], list[RawSkill]]
    load_existing_hashes: Callable[[], dict[str, str]]
    output_key: str = DEFAULT_OUTPUT_KEY

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        all_skills = self.load_raw_skills()
        existing = self.load_existing_hashes()
        changed = select_changed(all_skills, existing)

        skipped = len(all_skills) - len(changed)
        # JSON 化して session state に載せる（後段 Analyzer はここから読む）。
        payload = [s.model_dump(mode="json") for s in changed]

        yield Event(
            author=self.name,
            actions=EventActions(state_delta={self.output_key: payload}),
            custom_metadata={
                "collected": len(all_skills),
                "changed": len(changed),
                "skipped": skipped,
            },
        )
