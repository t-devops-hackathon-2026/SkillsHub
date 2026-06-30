"""ローカルファイルモードの収集源。

GitHub App（#8）の完了を待たずに Analyzer / 鮮度判定を開発できるよう、ローカルに
置いたサンプル SKILL.md 群を ``github_tools`` 経由の収集と同じ ``RawSkill`` に変換する。
``content_hash`` は ``github_tools.compute_content_hash`` を再利用して同一仕様に揃える。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from skillshub.shared.schemas import RawSkill
from skillshub.shared.tools.github_tools import SkillFile, compute_content_hash

_SKILL_FILENAME = "skill.md"


def load_local_skills(root: Path) -> list[RawSkill]:
    """``root`` 配下を走査し、SKILL.md のあるディレクトリごとに ``RawSkill`` を作る。

    - SKILL.md のあるディレクトリを 1 Skill 単位とみなす（ファイル名は大文字小文字許容）。
    - ``source_path`` / 関連ファイル名は ``root`` からの相対パスで表す。
    - ``last_commit_at`` はローカルにコミット情報が無いため SKILL.md の mtime で代用する。
    """
    root = root.resolve()
    skills: list[RawSkill] = []

    for skill_md_path in sorted(root.rglob("*")):
        if not skill_md_path.is_file() or skill_md_path.name.lower() != _SKILL_FILENAME:
            continue

        skill_dir = skill_md_path.parent
        files = [p for p in sorted(skill_dir.rglob("*")) if p.is_file()]

        skill_files = [SkillFile(path=str(p.relative_to(root)), content=p.read_bytes()) for p in files]
        related = [f.path for f in skill_files if Path(f.path).name.lower() != _SKILL_FILENAME]

        skills.append(
            RawSkill(
                source_path=str(skill_md_path.relative_to(root)),
                skill_md_text=skill_md_path.read_text(encoding="utf-8", errors="replace"),
                related_file_names=related,
                author=None,
                last_commit_at=datetime.fromtimestamp(skill_md_path.stat().st_mtime, tz=UTC),
                content_hash=compute_content_hash(skill_files),
            )
        )

    return skills
