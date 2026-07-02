"""鮮度（``update_status``）の確定判定。

コミット経過日数を主軸にコードで判定する（current / stale）。``needs_update`` は
「参照API・依存ツールの変更を検知」した場合で、Step1 では突き合わせ先データが無いため
Analyzer（LLM）が本文から見つけた古さ兆候フラグ（``is_possibly_outdated``）で代替する。

しきい値は ``config.get_stale_days()``（env ``UPDATE_STALE_DAYS``、既定 90）で調整可能。
"""

from __future__ import annotations

from datetime import UTC, datetime

from skillshub.shared.config import get_stale_days
from skillshub.shared.schemas import UpdateStatus


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

    stale_days = get_stale_days()

    now = datetime.now(UTC)
    # naive datetime が来た場合は UTC とみなして比較する。
    if last_commit_at.tzinfo is None:
        last_commit_at = last_commit_at.replace(tzinfo=UTC)
    elapsed_days = (now - last_commit_at).days

    if elapsed_days <= stale_days:
        return UpdateStatus.CURRENT
    return UpdateStatus.STALE
