"""司書収集バッチ（Cloud Run Jobs エントリ）。

登録リポジトリを巡回し、各リポジトリを司書パイプライン（収集→解析→鮮度→永続化→埋め込み→
重複検出）に通す。1 リポジトリの失敗で全体を止めず、リポジトリごとに件数内訳を構造化ログ
（JSON 1 行）で stdout に出す（Cloud Logging が構造化ログとして拾う）。成功時は
``last_collected_at`` を更新する（仕様: 失敗は status カラムでは扱わず、ログと最終収集時刻で
運用する。cf. docs/designs/step1/er.md）。

実行:

    uv run python -m skillshub.batch.run_collect                       # 全登録リポジトリ
    uv run python -m skillshub.batch.run_collect owner/repo            # GitHub 直指定（単一リポジトリ）
    uv run python -m skillshub.batch.run_collect owner                 # Org 配下を全列挙して収集
    uv run python -m skillshub.batch.run_collect --repo-id <uuid>      # DB 登録済みリポジトリを UUID 指定
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from skillshub.db.seed import _should_seed_demo_skills
from skillshub.shared import services
from skillshub.shared.agents.librarian import run_librarian_for_repo
from skillshub.shared.db import session_scope as _session_scope
from skillshub.shared.models import Repository
from skillshub.shared.schemas import RawSkill
from skillshub.shared.sources.github import load_github_skills
from skillshub.shared.sources.local import load_local_skills
from skillshub.shared.tools.ai_tools import EmbeddingFn

_SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "samples"
_LOCAL_OWNER = "local"
_LOCAL_REPO = "samples"


def _resolve_loader(owner: str, repo: str) -> Callable[[], list[RawSkill]]:
    """リポジトリに対応する収集源（``load_raw_skills``）を返す。

    owner が ``local`` ならローカルサンプルを、それ以外は GitHub App 経由で収集する。
    """
    if owner == _LOCAL_OWNER:
        return lambda: load_local_skills(_SAMPLES_ROOT)
    target = f"{owner}/{repo}"
    return lambda: load_github_skills(target)


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


def _resolve_github_targets(target: str) -> list[tuple[UUID, str, str]]:
    """GitHub target（``owner/repo`` or ``owner``）から収集対象のリポジトリリストを返す。

    DB に未登録のリポジトリは ``get_or_create_repository`` で自動登録する。
    owner のみ指定された場合は GitHub App のインストール配下を全列挙する。
    """
    if "/" in target:
        owner, repo = target.split("/", 1)
        repo_id = services.get_or_create_repository(owner, repo)
        return [(repo_id, owner, repo)]

    # owner のみ → Org のインストール配下を全列挙
    from skillshub.shared.tools.github_tools import (
        generate_app_jwt,
        get_installation_id_for_org,
        get_installation_token,
        list_installation_repositories,
    )

    org = target
    app_jwt = generate_app_jwt()
    installation_id = get_installation_id_for_org(app_jwt, org)
    token = get_installation_token(app_jwt, installation_id)
    result: list[tuple[UUID, str, str]] = []
    for repo_owner, repo_name, _default_branch in list_installation_repositories(token):
        repo_id = services.get_or_create_repository(repo_owner, repo_name)
        result.append((repo_id, repo_owner, repo_name))
    return result


def _expand_db_targets(rows: list[tuple[UUID, str, str]]) -> tuple[list[tuple[UUID, str, str]], int]:
    """DB 登録行を収集可能な対象へ整え、展開に失敗した Org の件数も返す。

    Org 登録行（repo=""）はインストール配下の実リポジトリへ展開し、手動登録の置き場
    （internal）は GitHub に実在しないためスキップする。展開で得た行と登録済み行の重複は除く。
    """
    expanded: list[tuple[UUID, str, str]] = []
    seen: set[UUID] = set()
    failed_orgs = 0
    for rid, owner, repo in rows:
        if owner == services.MANUAL_OWNER:
            continue
        if not repo:
            try:
                org_targets = _resolve_github_targets(owner)
            except Exception as exc:  # noqa: BLE001 - 1 Org の失敗で他の対象を止めない
                failed_orgs += 1
                print(json.dumps({"repo": owner, "status": "error", "error": str(exc)}, ensure_ascii=False))
                continue
            for org_target in org_targets:
                if org_target[0] not in seen:
                    seen.add(org_target[0])
                    expanded.append(org_target)
        elif rid not in seen:
            seen.add(rid)
            expanded.append((rid, owner, repo))
    return expanded, failed_orgs


def main(
    repo_id: str | None = None,
    target: str | None = None,
    *,
    embed_fn: EmbeddingFn | None = None,
) -> int:
    """登録リポジトリを巡回して収集パイプラインを回す。失敗リポジトリがあれば終了コード 1。

    ``target`` が指定された場合は GitHub 直指定モード（owner/repo または owner）。
    未指定の場合は DB 登録済みリポジトリを巡回するバッチモード。
    """
    repo_uuid = UUID(repo_id) if repo_id else None

    if target is not None:
        targets = _resolve_github_targets(target)
        failed_repos = 0
    else:
        # DB 巡回モード: 引数なし（定期バッチ）でもローカル完走できるよう local/samples を用意する。
        # samples/ はイメージにも同梱されるため、本番・staging では seed と同じガード
        # （SEED_DEMO_SKILLS=0）で登録をスキップする。ガード無しだと日次実行のたびに
        # 架空のデモ Skill が本番 DB に取り込まれてしまう。
        if repo_uuid is None and _should_seed_demo_skills():
            services.get_or_create_repository(_LOCAL_OWNER, _LOCAL_REPO)
        targets, failed_repos = _expand_db_targets(_target_repos(repo_uuid))

    if not targets and not failed_repos:
        msg = {"summary": "対象リポジトリがありません", "repo_id": repo_id, "target": target}
        print(json.dumps(msg, ensure_ascii=False))
        return 0

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
        loader = _resolve_loader(owner, repo_name)

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
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="GitHub target: owner/repo（単一）または owner（Org 全体）。省略時は DB 登録済み全リポジトリを巡回",
    )
    parser.add_argument(
        "--repo-id",
        default=None,
        help="DB 登録済みリポジトリの UUID（target 省略時のみ有効。省略時は全登録リポジトリを巡回）",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    raise SystemExit(main(args.repo_id, args.target))
