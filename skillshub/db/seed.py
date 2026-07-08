"""初期シード投入スクリプト（冪等）。

投入物（issue #11 完了条件）:
- 空の Repository 1件 … 登録済みだが未収集のリポジトリ。画面⑤「今すぐ収集」のデモ用。
- 手動 Skill 2件 … 検索デモ用の題材（議事録要約 / タスク抽出）。
  Skill は repo_id が NOT NULL のため、手動登録用のリポジトリ 1件にぶら下げる。
  架空データのため、本番では ``SEED_DEMO_SKILLS=0`` で投入をスキップできる
  （空 Repository は実在リポジトリなので常に投入する）。

実行: `uv run python -m skillshub.db.seed`
何度流しても重複しない（owner/repo・(repo_id, name) で存在チェックしてから挿入）。
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from skillshub.shared.db import _get_engine
from skillshub.shared.models import Repository, Skill
from skillshub.shared.schemas import UpdateStatus


def _get_or_create_repository(session: Session, *, owner: str, repo: str, install_id: str | None) -> Repository:
    existing = session.scalar(select(Repository).where(Repository.owner == owner, Repository.repo == repo))
    if existing is not None:
        return existing
    repository = Repository(owner=owner, repo=repo, install_id=install_id)
    session.add(repository)
    session.flush()  # id を採番してから skill に紐付ける
    return repository


def _get_or_create_skill(session: Session, *, repo_id: UUID, name: str, **fields: Any) -> Skill:
    existing = session.scalar(select(Skill).where(Skill.repo_id == repo_id, Skill.name == name))
    if existing is not None:
        return existing
    skill = Skill(repo_id=repo_id, name=name, **fields)
    session.add(skill)
    return skill


def _should_seed_demo_skills() -> bool:
    """デモ Skill を投入するかどうか（既定 True、env ``SEED_DEMO_SKILLS``）。

    env 未設定・空文字の場合は既定値を使う（config.get_secret と同じく空文字は未設定扱い）。
    """
    value = os.environ.get("SEED_DEMO_SKILLS")
    if not value:
        return True
    return value.lower() not in {"0", "false"}


def seed_into(session: Session) -> None:
    """与えられた session に初期データを投入する（commit は呼び出し側の責務）。

    デモリセット（services.reset_demo_data）が全削除と同一トランザクションで
    呼べるよう、session 管理を seed() から分離してある。ガードをここに置くことで、
    migrate 経由でもリセットボタン経由でも本番にデモ Skill が入らないようにする。
    """
    # 収集デモ用の空リポジトリ（Skill を持たない）。実在リポジトリなので常に投入する。
    _get_or_create_repository(session, owner="t-devops-hackathon-2026", repo="SkillsHub", install_id=None)

    if not _should_seed_demo_skills():
        return

    # 手動 Skill を載せるリポジトリ（手動登録の置き場）。
    manual_repo = _get_or_create_repository(session, owner="internal", repo="manual-skills", install_id=None)

    _get_or_create_skill(
        session,
        repo_id=manual_repo.id,
        name="議事録要約 Skill",
        description="会議の議事録を自動で要約し、要点を箇条書きにする",
        source_path="skills/meeting-summarizer/SKILL.md",
        author="alice",
        tags=["議事録", "要約", "会議"],
        usage="会議の議事録テキストを入力すると、要点を3〜5行に要約します。",
        update_status=UpdateStatus.CURRENT,
    )
    _get_or_create_skill(
        session,
        repo_id=manual_repo.id,
        name="タスク抽出 Skill",
        description="会議メモやチャットログからアクションアイテムを抽出する",
        source_path="skills/task-extractor/SKILL.md",
        author="bob",
        tags=["タスク", "抽出", "会議"],
        usage="テキストを入力すると、TODO / アクションアイテムをリストアップします。",
        update_status=UpdateStatus.STALE,
    )


def seed() -> None:
    with Session(_get_engine()) as session:
        seed_into(session)
        session.commit()
    if _should_seed_demo_skills():
        print("seed 完了: repositories=2, skills=2")
    else:
        print("seed 完了: repositories=1, skills=0（SEED_DEMO_SKILLS によりデモ Skill をスキップ）")


if __name__ == "__main__":
    seed()
