"""司書バッチ②: 埋め込み生成と重複検出（merge 提案）の独立エントリポイント。

DB の skills を列挙し、各 Skill について埋め込み生成→近傍探索→merge 提案生成を回す。
1 件の失敗で全体を止めず、件数と生成提案数をログ出力する。

実行:

    uv run python -m skillshub.batch.run_dedup            # 全 Skill
    uv run python -m skillshub.batch.run_dedup --repo-id <uuid>  # 指定リポジトリのみ

将来 #14/#16 と合流する際は ``shared.agents.build_deduper_agent`` を司書
SequentialAgent に組み込み、このバッチをそこへ吸収できる。
"""

from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import select

from skillshub.shared.agents.deduper import run_deduper_for_skill
from skillshub.shared.db import get_session
from skillshub.shared.models import Skill

# get_session は commit/rollback/close を内包するジェネレータ。with で使うため CM 化する。
_session_scope = contextmanager(get_session)


def main(repo_id: str | None = None) -> int:
    repo_uuid = UUID(repo_id) if repo_id else None

    processed = 0
    suggestions = 0
    failures = 0

    with _session_scope() as session:
        stmt = select(Skill).order_by(Skill.created_at)
        if repo_uuid is not None:
            stmt = stmt.where(Skill.repo_id == repo_uuid)

        for skill in session.scalars(stmt).all():
            try:
                created = run_deduper_for_skill(session, skill)
                processed += 1
                suggestions += len(created)
            except Exception as exc:  # noqa: BLE001 — 1件の失敗で他Skillを止めない
                failures += 1
                print(f"[WARN] Skill {skill.id}（{skill.name}）の処理に失敗: {exc}", file=sys.stderr)

    print(f"=== dedup 完了: 処理 {processed} 件 / merge 提案 {suggestions} 件 / 失敗 {failures} 件 ===")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="埋め込み生成と重複検出バッチ")
    parser.add_argument("--repo-id", default=None, help="対象リポジトリの UUID（省略時は全 Skill）")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    raise SystemExit(main(args.repo_id))
