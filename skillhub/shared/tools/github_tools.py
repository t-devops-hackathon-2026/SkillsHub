"""GitHub App 経由で SKILL.md 一式を収集するツール群（雛形）。

実装方針（DESIGN.md「GitHub App 連携」参照）:
- App秘密鍵(Secret Manager)→JWT→Installation Access Token で認証
- GET /installation/repositories で対象リポジトリを列挙
- Git Trees API(recursive=1) で SKILL.md を探索し、置かれたディレクトリを1 Skill 単位とする
- content_hash(SHA-256) で差分検知し、変更分のみ後段に渡す
"""
from __future__ import annotations


def list_skill_dirs(repo_id: str) -> list[str]:
    """リポジトリ内の SKILL.md ディレクトリ一覧を返す。

    Args:
        repo_id: REPOSITORY テーブルの id。
    Returns:
        SKILL.md が置かれたディレクトリパスのリスト。
    """
    # TODO: PyGithub / Git Trees API で実装
    raise NotImplementedError


def fetch_skill(repo_id: str, source_path: str) -> dict:
    """1つの Skill ディレクトリ（SKILL.md + 関連ファイル）を取得する。

    Returns:
        shared.schemas.RawSkill 相当の dict。
    """
    # TODO: Contents API + Commits API で実装し content_hash を計算
    raise NotImplementedError
