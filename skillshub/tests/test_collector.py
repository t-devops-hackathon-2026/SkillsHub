from __future__ import annotations

import pytest

from skillshub.shared.agents.collector import select_changed
from skillshub.shared.schemas import RawSkill


def _raw(source_path: str, content_hash: str) -> RawSkill:
    return RawSkill(
        source_path=source_path,
        skill_md_text="# dummy",
        content_hash=content_hash,
    )


@pytest.mark.unit
def test_new_skill_is_selected() -> None:
    raws = [_raw("a/SKILL.md", "h1")]
    changed = select_changed(raws, existing_hashes={})
    assert [s.source_path for s in changed] == ["a/SKILL.md"]


@pytest.mark.unit
def test_unchanged_skill_is_skipped() -> None:
    raws = [_raw("a/SKILL.md", "h1")]
    changed = select_changed(raws, existing_hashes={"a/SKILL.md": "h1"})
    assert changed == []


@pytest.mark.unit
def test_changed_hash_is_selected() -> None:
    raws = [_raw("a/SKILL.md", "h2")]
    changed = select_changed(raws, existing_hashes={"a/SKILL.md": "h1"})
    assert [s.source_path for s in changed] == ["a/SKILL.md"]


@pytest.mark.unit
def test_mixed_only_changed_pass_through() -> None:
    raws = [
        _raw("keep/SKILL.md", "same"),
        _raw("new/SKILL.md", "n1"),
        _raw("edit/SKILL.md", "v2"),
    ]
    existing = {"keep/SKILL.md": "same", "edit/SKILL.md": "v1"}
    changed = select_changed(raws, existing_hashes=existing)
    assert sorted(s.source_path for s in changed) == ["edit/SKILL.md", "new/SKILL.md"]
