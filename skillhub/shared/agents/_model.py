"""エージェント共通のモデル設定。

モデルIDは最新世代に追従させたいので環境変数で上書き可能にする。
既定値は DESIGN.md の技術スタック（Gemini Flash 既定・重い推論のみ Pro）に対応。
※ 実際に利用可能なモデルIDは Vertex AI のコンソールで確認してから固定すること。
"""
from __future__ import annotations

import os

# 軽量・既定（解析/採点/検索など大半の処理）
FLASH_MODEL = os.getenv("SKILLHUB_FLASH_MODEL", "gemini-2.5-flash")

# 重い推論（合成提案・改善 diff など）。余力次第で使い分ける
PRO_MODEL = os.getenv("SKILLHUB_PRO_MODEL", "gemini-2.5-pro")
