from __future__ import annotations

from pathlib import Path

import pytest

from skillshub.shared.sources.local import load_local_skills


def _write_skill(root: Path, rel_dir: str, body: str, extra: dict[str, str] | None = None) -> None:
    skill_dir = root / rel_dir
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    for name, content in (extra or {}).items():
        (skill_dir / name).write_text(content, encoding="utf-8")


@pytest.mark.unit
def test_loads_skill_md_with_related_files(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "skills/foo",
        "# Foo\nfoo の説明",
        extra={"reference.md": "参考"},
    )

    skills = load_local_skills(tmp_path)

    assert len(skills) == 1
    skill = skills[0]
    assert skill.source_path == "skills/foo/SKILL.md"
    assert "foo の説明" in skill.skill_md_text
    assert skill.related_file_names == ["skills/foo/reference.md"]
    assert skill.content_hash  # 空でない


@pytest.mark.unit
def test_finds_multiple_skills_case_insensitive(tmp_path: Path) -> None:
    _write_skill(tmp_path, "a", "# A")
    _write_skill(tmp_path, "b", "# B")
    # ファイル名の大文字小文字は許容する。
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "Skill.md").write_text("# C", encoding="utf-8")

    skills = load_local_skills(tmp_path)

    assert {s.source_path for s in skills} == {"a/SKILL.md", "b/SKILL.md", "c/Skill.md"}


@pytest.mark.unit
def test_content_hash_is_stable_and_changes_with_content(tmp_path: Path) -> None:
    _write_skill(tmp_path, "x", "# X")
    first = load_local_skills(tmp_path)[0].content_hash
    again = load_local_skills(tmp_path)[0].content_hash
    assert first == again

    (tmp_path / "x" / "SKILL.md").write_text("# X changed", encoding="utf-8")
    changed = load_local_skills(tmp_path)[0].content_hash
    assert changed != first


@pytest.mark.unit
def test_empty_root_returns_empty(tmp_path: Path) -> None:
    assert load_local_skills(tmp_path) == []
