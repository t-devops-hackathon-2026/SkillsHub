"""オンライン（対話）エージェント。

アプリ（Streamlit）のリクエスト時に Runner で個別に起動する。
- searcher_agent: 自然言語検索の主役（必須）
- composer_agent: 候補2件以上のときの合成提案（任意）
- improver_agent: 詳細画面からの改善 diff 提案（任意）

合成は「Searcher → Composer」の順だが、Composer は構造化出力（output_schema）の
ため tools を持てない。よって SequentialAgent で機械的に繋ぐより、サービス層
(shared.services) が Searcher の結果を見て必要時に Composer を呼ぶ方が制御しやすい。
ここでは各エージェントを公開するだけに留める。
"""
from __future__ import annotations

from .composer import composer_agent
from .improver import improver_agent
from .searcher import searcher_agent

__all__ = ["searcher_agent", "composer_agent", "improver_agent"]
