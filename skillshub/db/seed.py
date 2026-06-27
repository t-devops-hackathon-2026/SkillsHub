"""初期シード投入スクリプト（冪等）。

投入物（issue #11 完了条件）:
- 空の Repository 1件 … 登録済みだが未収集のリポジトリ。画面⑤「今すぐ収集」のデモ用。
- 手動 Skill 2件 … 検索デモ用の題材（議事録要約 / タスク抽出）。
  Skill は repo_id が NOT NULL のため、手動登録用のリポジトリ 1件にぶら下げる。

実行: `uv run python -m skillshub.db.seed`
何度流しても重複しない（owner/repo・(repo_id, name) で存在チェックしてから挿入）。
"""

from __future__ import annotations

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


def _get_or_create_skill(session: Session, *, repo_id, name: str, **fields) -> Skill:  # type: ignore[no-untyped-def]
    existing = session.scalar(select(Skill).where(Skill.repo_id == repo_id, Skill.name == name))
    if existing is not None:
        return existing
    skill = Skill(repo_id=repo_id, name=name, **fields)
    session.add(skill)
    return skill


def seed() -> None:
    with Session(_get_engine()) as session:
        # 手動 Skill を載せるリポジトリ（手動登録の置き場）。
        manual_repo = _get_or_create_repository(session, owner="internal", repo="manual-skills", install_id=None)
        # 収集デモ用の空リポジトリ（Skill を持たない）。
        _get_or_create_repository(session, owner="t-devops-hackathon-2026", repo="ai-agent", install_id=None)

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

        session.commit()
    print("seed 完了: repositories=2, skills=2")


if __name__ == "__main__":
    seed()
