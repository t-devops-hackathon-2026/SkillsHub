"""SkillsHub の ADK エージェント群。

合意した構成（5体 + 司書オーケストレータ）:

  オフライン（司書 = SequentialAgent）
    1. CollectorAgent  … リポジトリ走査・SKILL.md 取得（tools）
    2. AnalyzerAgent   … Parser+Scorer 統合・構造化採点（output_schema）
    3. DeduperAgent    … 類似検出→merge 提案（tools）

  オンライン（アプリから起動）
    4. SearcherAgent   … 自然言語検索 / RAG（tools, 必須）
    5. ComposerAgent   … 合成提案（output_schema, 任意）
       ImproverAgent   … 改善 diff 提案（output_schema, 任意）

FreshnessAgent は当面 Analyzer 内の仮判定＋ルールで代替し、必要になれば独立させる。
"""
from .collector import collector_agent
from .analyzer import analyzer_agent
from .deduper import deduper_agent
from .librarian import librarian_agent
from .searcher import searcher_agent
from .composer import composer_agent
from .improver import improver_agent

__all__ = [
    "collector_agent",
    "analyzer_agent",
    "deduper_agent",
    "librarian_agent",
    "searcher_agent",
    "composer_agent",
    "improver_agent",
]
