from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from skillshub.shared.schemas import UpdateStatus
from skillshub.shared.update_status import compute_update_status


def _days_ago(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


@pytest.fixture(autouse=True)
def _default_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    # 既定しきい値（90）を明示設定し、開発環境の .env に依存しないようにする。
    monkeypatch.setenv("UPDATE_STALE_DAYS", "90")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, UpdateStatus.CURRENT),
        (89, UpdateStatus.CURRENT),
        (90, UpdateStatus.CURRENT),  # 境界: 90日"以内"は current
        (91, UpdateStatus.STALE),
        (179, UpdateStatus.STALE),
        (180, UpdateStatus.STALE),
        (181, UpdateStatus.STALE),  # 180日超も陳腐化注意として stale（current には戻さない）
    ],
)
def test_days_based_status(days: int, expected: UpdateStatus) -> None:
    assert compute_update_status(_days_ago(days), is_possibly_outdated=False) == expected


@pytest.mark.unit
def test_outdated_flag_takes_priority_over_days() -> None:
    # まだ新しくても、依存の古さ兆候があれば needs_update が優先される。
    assert compute_update_status(_days_ago(1), is_possibly_outdated=True) == UpdateStatus.NEEDS_UPDATE


@pytest.mark.unit
def test_missing_commit_date_is_stale() -> None:
    # コミット日時が不明なものは安全側に倒して stale 扱い。
    assert compute_update_status(None, is_possibly_outdated=False) == UpdateStatus.STALE


@pytest.mark.unit
def test_missing_commit_date_with_outdated_flag_is_needs_update() -> None:
    assert compute_update_status(None, is_possibly_outdated=True) == UpdateStatus.NEEDS_UPDATE


@pytest.mark.unit
def test_thresholds_are_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDATE_STALE_DAYS", "30")
    # 既定(90)なら current だが、しきい値 30 では stale になる。
    assert compute_update_status(_days_ago(45), is_possibly_outdated=False) == UpdateStatus.STALE
