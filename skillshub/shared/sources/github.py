"""GitHub 収集源 — CollectedSkill → RawSkill 変換アダプタ。

``github_tools.collect_skills`` が返す ``CollectedSkill``（バイト列を持つ dataclass）を
ADK セッション状態に載せられる ``RawSkill``（テキストのみ）へ変換する。

ローカル収集源（``local.py``）と同じ ``list[RawSkill]`` を返すため、
``run_librarian_for_repo`` の ``load_raw_skills`` 引数に差し替え可能。
"""

from __future__ import annotations

from skillshub.shared.schemas import RawSkill
from skillshub.shared.tools.github_tools import CollectedSkill, collect_skills


def collected_to_raw(cs: CollectedSkill) -> RawSkill:
    """CollectedSkill（バイト列含む）を後段パイプライン用の RawSkill へ変換する。"""
    return RawSkill(
        source_path=cs.source_path,
        skill_md_text=cs.skill_md.text,
        related_file_names=[f.path for f in cs.related_files],
        author=cs.author,
        last_commit_at=cs.last_commit_at,
        content_hash=cs.content_hash,
    )


def load_github_skills(target: str) -> list[RawSkill]:
    """``owner/repo`` または ``owner`` を指定して GitHub から RawSkill を収集する。

    認証は ``github_tools.collect_skills`` が App 秘密鍵 → JWT → Installation Token の順で行う。
    """
    return [collected_to_raw(cs) for cs in collect_skills(target)]
