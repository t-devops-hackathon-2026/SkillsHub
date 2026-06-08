"""司書（オフライン自律バッチ）。

Cloud Scheduler → Cloud Run Jobs から起動される自律ループの本体。
Collector → Analyzer → Deduper を順に実行する SequentialAgent。

埋め込み生成は ai_tools.embed_text をバッチ手続き側（batch/run_collect.py）で
呼ぶか、Deduper の前段にツール実行ステップとして挟む。ここでは LLM を伴う
3エージェントの順序づけのみを定義する（DESIGN.md「ADKエージェント設計」参照）。
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from .analyzer import analyzer_agent
from .collector import collector_agent
from .deduper import deduper_agent

librarian_agent = SequentialAgent(
    name="LibrarianAgent",
    sub_agents=[
        collector_agent,
        analyzer_agent,
        deduper_agent,
    ],
)
