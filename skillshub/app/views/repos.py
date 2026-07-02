from __future__ import annotations

import streamlit as st

from skillshub.shared import services

_MSG_KEY = "repos_flash_message"


def _set_flash(level: str, text: str) -> None:
    st.session_state[_MSG_KEY] = {"level": level, "text": text}


def _render_flash() -> None:
    msg = st.session_state.pop(_MSG_KEY, None)
    if msg is None:
        return
    level = msg["level"]
    if level == "success":
        st.success(msg["text"])
    elif level == "error":
        st.error(msg["text"])
    elif level == "warning":
        st.warning(msg["text"])
    else:
        st.info(msg["text"])


def _parse_owner_repo(raw: str) -> tuple[str, str] | None:
    """``owner/repo`` 形式を検証してタプルを返す。不正な場合は None。"""
    parts = raw.strip().split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0].strip(), parts[1].strip()


def _render_register_form() -> None:
    st.subheader("リポジトリを追加")

    with st.form("register_repo_form", clear_on_submit=True):
        raw = st.text_input(
            "GitHub リポジトリ",
            placeholder="owner/repo　例: t-devops-hackathon-2026/ai-agent",
        )
        submitted = st.form_submit_button("登録する", use_container_width=True, type="primary")

    if not submitted:
        return

    if not raw:
        st.warning("owner/repo を入力してください。")
        return

    parsed = _parse_owner_repo(raw)
    if parsed is None:
        st.error("「owner/repo」の形式で入力してください。（例: my-org/my-repo）")
        return

    owner, repo = parsed
    try:
        services.get_or_create_repository(owner, repo)
        _set_flash("success", f"✅ `{owner}/{repo}` を登録しました。")
        st.rerun()
    except Exception as exc:
        st.error(f"登録に失敗しました: {exc}")


def _render_repo_list() -> None:
    repos = services.list_repositories()

    if not repos:
        st.info("登録済みリポジトリがありません。上のフォームから追加してください。")
        return

    st.subheader(f"登録済みリポジトリ（{len(repos)} 件）")

    for r in repos:
        owner = str(r["owner"])
        repo = str(r["repo"])
        last_at = r["last_collected_at"]
        skill_count = r["skill_count"]
        repo_id = str(r["id"])

        clicked = False
        with st.container(border=True, key=f"repo_box_{repo_id}"):
            col_info, col_btn = st.columns([5, 1])

            with col_info:
                st.markdown(f"**{owner}/{repo}**")
                last_str = last_at.strftime("%Y-%m-%d %H:%M") if last_at else "未収集"
                st.caption(f"最終収集: {last_str}　｜　Skill: {skill_count} 件")

            with col_btn:
                # 擬似 owner（local samples / 手動登録の置き場）は GitHub に実在しないため
                # 「今すぐ収集」を出さない（collect_repo が installation 取得の 404 で落ちる）。
                if owner not in services.PSEUDO_OWNERS:
                    clicked = st.button("今すぐ収集", key=f"collect_{repo_id}", use_container_width=True)
                else:
                    st.caption("GitHub 収集の対象外")

        if clicked:
            with st.spinner(f"{owner}/{repo} を収集中…"):
                try:
                    result = services.collect_repo(repo_id)
                    collected = result["collected_skills"]
                    skipped = result["skipped_skills"]
                    _set_flash(
                        "success",
                        f"✅ `{owner}/{repo}` の収集が完了しました。　取得: {collected} 件　／　スキップ: {skipped} 件",
                    )
                    st.rerun()
                except Exception as exc:
                    _set_flash("error", f"❌ `{owner}/{repo}` の収集に失敗しました。\n{exc}")
                    st.rerun()


def render() -> None:
    st.subheader("収集元を追加する")
    st.caption("GitHub App がインストールされたリポジトリを登録すると、エージェントがスキル（SKILL.md）を収集します。")
    st.divider()

    _render_flash()
    _render_register_form()
    st.divider()
    _render_repo_list()
