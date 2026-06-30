"""司書収集バッチ（Cloud Run Jobs エントリ）。

登録リポジトリを巡回し、各リポジトリを司書パイプライン（収集→解析→鮮度→永続化→埋め込み→
重複検出）に通す。1 リポジトリの失敗で全体を止めず、リポジトリごとに件数内訳を構造化ログ
（JSON 1 行）で stdout に出す（Cloud Logging が構造化ログとして拾う）。成功時は
``last_collected_at`` を更新する（仕様: 失敗は status カラムでは扱わず、ログと最終収集時刻で
運用する。cf. docs/designs/step1/er.md）。

実行:

    uv run python -m skillshub.batch.run_collect                   # 全登録リポジトリ
    uv run python -m skillshub.batch.run_collect --repo-id <uuid>  # 指定リポジトリのみ（即時収集）

#16 はローカル収集源（``samples/``）で 1 ループ完走することを範囲とする。実 GitHub からの
収集配線（owner が ``local`` 以外の収集源解決）は #41 が担当する。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from skillshub.shared import services
from skillshub.shared.agents.librarian import run_librarian_for_repo
from skillshub.shared.db import get_session
from skillshub.shared.models import Repository
from skillshub.shared.schemas import RawSkill
from skillshub.shared.sources.local import load_local_skills
from skillshub.shared.tools.ai_tools import EmbeddingFn

# get_session は commit/rollback/close を内包するジェネレータ。with で使うため CM 化する。
_session_scope = contextmanager(get_session)

# ローカル収集源（#16）。GitHub 収集源の解決は #41。
_SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "samples"
_LOCAL_OWNER = "local"
_LOCAL_REPO = "samples"


def _resolve_loader(owner: str) -> Callable[[], list[RawSkill]] | None:
    """リポジトリに対応する収集源（``load_raw_skills``）を返す。#16 はローカルのみ対応。

    GitHub 収集源（owner が ``local`` 以外）は #41 で配線するため、ここでは ``None`` を返し、
    呼び出し側はそのリポジトリを「収集源未対応」としてスキップ・ログ記録する。
    """
    if owner == _LOCAL_OWNER:
        return lambda: load_local_skills(_SAMPLES_ROOT)
    return None


def _existing_hashes_loader(repo_id: UUID) -> Callable[[], dict[str, str]]:
    """指定リポジトリの既存 content_hash を返すローダ（巡回ループでの遅延束縛を避ける）。"""
    return lambda: services.get_existing_content_hashes(repo_id)


def _target_repos(repo_id: UUID | None) -> list[tuple[UUID, str, str]]:
    """巡回対象リポジトリの ``(id, owner, repo)`` を返す。指定時は1件、未指定なら全件。

    ORM を session 外へ持ち出さないよう、必要な値だけタプルに退避して返す（各リポジトリの
    収集は ``run_librarian_for_repo`` が独自 session で行うため、ここでは一覧取得に徹する）。
    """
    with _session_scope() as session:
        if repo_id is not None:
            repo = session.get(Repository, repo_id)
            repos = [repo] if repo is not None else []
        else:
            repos = list(session.scalars(select(Repository).order_by(Repository.created_at)).all())
        return [(r.id, r.owner, r.repo) for r in repos]


def main(repo_id: str | None = None, *, embed_fn: EmbeddingFn | None = None) -> int:
    """登録リポジトリを巡回して収集パイプラインを回す。失敗リポジトリがあれば終了コード 1。"""
    repo_uuid = UUID(repo_id) if repo_id else None

    # 引数なし（定期バッチ）でもローカル完走できるよう、ローカル収集源のリポジトリを用意する。
    # 実 GitHub リポジトリの巡回は #41 で収集源解決を拡張する。
    if repo_uuid is None:
        services.get_or_create_repository(_LOCAL_OWNER, _LOCAL_REPO)

    targets = _target_repos(repo_uuid)
    if not targets:
        print(json.dumps({"summary": "対象リポジトリがありません", "repo_id": repo_id}, ensure_ascii=False))
        return 0

    failed_repos = 0
    for rid, owner, repo_name in targets:
        log: dict[str, object] = {
            "repo": f"{owner}/{repo_name}",
            "status": "ok",
            "collected": 0,
            "skipped": 0,
            "needs_update": 0,
            "merge_suggestions": 0,
            "failed": 0,
        }
        loader = _resolve_loader(owner)
        if loader is None:
            log["status"] = "skipped"
            log["reason"] = "収集源未対応（GitHub は #41）"
            print(json.dumps(log, ensure_ascii=False))
            continue

        try:
            run_result = run_librarian_for_repo(
                rid,
                loader,
                _existing_hashes_loader(rid),
                embed_fn=embed_fn,
            )
            services.touch_last_collected_at(rid)
            s = run_result.stats
            log.update(
                collected=s.collected,
                skipped=s.skipped,
                needs_update=s.needs_update,
                merge_suggestions=s.merge_suggestions,
                failed=s.failed,
            )
        except Exception as exc:  # noqa: BLE001 - 1 リポジトリの失敗で全体（他リポジトリ）を止めない
            failed_repos += 1
            log["status"] = "error"
            log["error"] = str(exc)
        print(json.dumps(log, ensure_ascii=False))

    print(
        json.dumps(
            {"summary": {"repositories": len(targets), "failed_repositories": failed_repos}},
            ensure_ascii=False,
        )
    )
    return 1 if failed_repos else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="司書収集バッチ（Cloud Run Jobs エントリ）")
    parser.add_argument("--repo-id", default=None, help="対象リポジトリの UUID（省略時は全登録リポジトリを巡回）")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    raise SystemExit(main(args.repo_id))
