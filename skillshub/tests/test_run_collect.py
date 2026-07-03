"""収集バッチ（run_collect）の DB 巡回対象の展開ロジックのテスト。

Org 登録行（repo=""）の展開・手動置き場（internal）のスキップ・重複排除を、
GitHub には触らず ``_resolve_github_targets`` の monkeypatch で検証する。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from skillshub.batch import run_collect


def test_expand_db_targets_expands_org_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Org 行は配下リポジトリへ展開され、個別登録済み行との重複は除かれる。"""
    repo_a_id = uuid4()
    repo_b_id = uuid4()

    def fake_resolve(target: str) -> list[tuple[UUID, str, str]]:
        assert target == "acme"
        return [(repo_a_id, "acme", "a"), (repo_b_id, "acme", "b")]

    monkeypatch.setattr(run_collect, "_resolve_github_targets", fake_resolve)

    rows = [
        (repo_b_id, "acme", "b"),  # 個別登録済み（Org 展開結果と重複）
        (uuid4(), "internal", "manual"),  # 手動登録の置き場 → スキップ
        (uuid4(), "acme", ""),  # Org 登録行 → 展開
    ]
    targets, failed_orgs = run_collect._expand_db_targets(rows)

    assert failed_orgs == 0
    assert {t[0] for t in targets} == {repo_a_id, repo_b_id}
    assert len(targets) == 2


def test_expand_db_targets_counts_failed_org(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Org の列挙に失敗しても例外にせず、失敗件数として返す（他の対象は巡回を続ける）。"""

    def fake_resolve(target: str) -> list[tuple[UUID, str, str]]:
        raise RuntimeError("no installation")

    monkeypatch.setattr(run_collect, "_resolve_github_targets", fake_resolve)

    repo_id = uuid4()
    rows = [(uuid4(), "acme", ""), (repo_id, "o", "r")]
    targets, failed_orgs = run_collect._expand_db_targets(rows)

    assert failed_orgs == 1
    assert targets == [(repo_id, "o", "r")]
    assert "no installation" in capsys.readouterr().out
