"""鮮度（``update_status``）の確定判定。

コミット経過日数を主軸にコードで判定する（current / stale）。``needs_update`` は
「参照API・依存ツールの変更を検知」した場合で、Step1 では突き合わせ先データが無いため
Analyzer（LLM）が本文から見つけた古さ兆候フラグ（``is_possibly_outdated``）で代替する。

しきい値は環境変数で調整可能（仕様: 90 は暫定値、実データで調整する前提）。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from skillshub.shared.schemas import UpdateStatus

_DEFAULT_STALE_DAYS = 90


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:  # None・空文字はどちらも未設定扱い
        return default
    return int(raw)


def compute_update_status(
    last_commit_at: datetime | None,
    *,
    is_possibly_outdated: bool,
) -> UpdateStatus:
    """最終コミット日時と古さ兆候から ``update_status`` を決める。

    - 古さ兆候あり → ``needs_update``（日数に関わらず最優先）
    - 経過 ≤ UPDATE_STALE_DAYS(既定90) → ``current``
    - それ以外（しきい値超）→ ``stale``
    - コミット日時不明 → 安全側で ``stale``
    """
    if is_possibly_outdated:
        return UpdateStatus.NEEDS_UPDATE

    if last_commit_at is None:
        return UpdateStatus.STALE

    stale_days = _env_int("UPDATE_STALE_DAYS", _DEFAULT_STALE_DAYS)

    now = datetime.now(UTC)
    # naive datetime が来た場合は UTC とみなして比較する。
    if last_commit_at.tzinfo is None:
        last_commit_at = last_commit_at.replace(tzinfo=UTC)
    elapsed_days = (now - last_commit_at).days

    if elapsed_days <= stale_days:
        return UpdateStatus.CURRENT
    return UpdateStatus.STALE
