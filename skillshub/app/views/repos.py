from __future__ import annotations

import streamlit as st

from skillshub.shared import services

_MSG_KEY          = "repos_flash_message"
_GO_DASH_KEY      = "repos_go_dashboard"
_PENDING_COLLECT  = "repos_pending_collect"   # 登録直後の「今すぐ収集」待ち

_KIND_ORG_BADGE = (
    '<span style="background:#fbefff;color:#8250df;font-size:11px;font-weight:600;'
    'padding:2px 8px;border-radius:2em;white-space:nowrap">Org</span>'
)
_KIND_REPO_BADGE = (
    '<span style="background:#ddf4ff;color:#0969da;font-size:11px;font-weight:600;'
    'padding:2px 8px;border-radius:2em;white-space:nowrap">repo</span>'
)

_COL_WIDTHS  = [3, 1, 1.5, 2, 0.8, 1.5, 1.2]
_COL_HEADERS = ["対象", "種別", "ブランチ", "最終収集", "Skills", "状態", ""]


def _set_flash(level: str, text: str, go_dashboard: bool = False) -> None:
    st.session_state[_MSG_KEY] = {"level": level, "text": text}
    if go_dashboard:
        st.session_state[_GO_DASH_KEY] = True


def _collect(repo_id: str, display_name: str, kind: str, owner: str) -> None:
    """収集を実行してフラッシュメッセージをセットする。"""
    with st.spinner(f"{display_name} を収集中…"):
        try:
            if kind == "org":
                from skillshub.batch.run_collect import main as batch_main

                exit_code = batch_main(target=owner)
                if exit_code != 0:
                    raise RuntimeError("一部のリポジトリの収集に失敗しました。")
                _set_flash("success", f"✅ `{owner}` Org の収集が完了しました。", go_dashboard=True)
            else:
                result    = services.collect_repo(repo_id)
                collected = result["collected_skills"]
                skipped   = result["skipped_skills"]
                _set_flash(
                    "success",
                    f"✅ `{display_name}` の収集が完了しました。"
                    f"　取得: {collected} 件　／　スキップ: {skipped} 件",
                    go_dashboard=True,
                )
        except Exception as exc:
            _set_flash("error", f"❌ `{display_name}` の収集に失敗しました。\n{exc}")
    st.rerun()


def _render_flash() -> None:
    # メッセージは1度だけ表示して消す
    msg = st.session_state.pop(_MSG_KEY, None)
    if msg is not None:
        getattr(st, msg["level"])(msg["text"])

    # 登録直後: 「今すぐ収集」ボタンをクリックされるまで表示し続ける
    pending = st.session_state.get(_PENDING_COLLECT)
    if pending:
        col_btn, col_skip, _ = st.columns([1.5, 1, 5])
        if col_btn.button("今すぐ収集", type="primary", key="collect_after_register_btn"):
            st.session_state.pop(_PENDING_COLLECT, None)
            _collect(
                pending["repo_id"],
                pending["display_name"],
                pending["kind"],
                pending["owner"],
            )
        if col_skip.button("あとで", key="skip_collect_btn"):
            st.session_state.pop(_PENDING_COLLECT, None)
            st.rerun()

    # 収集完了後: 「ダッシュボードで確認」ボタンをクリックされるまで表示し続ける
    if st.session_state.get(_GO_DASH_KEY):
        if st.button("ダッシュボードで確認 →", type="primary", key="go_dash_btn"):
            st.session_state.pop(_GO_DASH_KEY, None)
            st.session_state.current_view = "dashboard"
            st.rerun()


def _kind(repo: str) -> str:
    return "org" if not repo else "repo"


def _render_register_form() -> None:
    col_kind, col_name, col_branch, col_btn = st.columns([1.8, 3, 1.5, 1])

    with col_kind:
        kind = st.selectbox(
            "種別",
            ["owner/repo", "Organization"],
            key="repo_kind_select",
            label_visibility="collapsed",
        )

    is_org = kind == "Organization"

    with col_name:
        raw = st.text_input(
            "対象",
            placeholder="例: example-corp" if is_org else "例: owner/repo",
            key="repo_name_input",
            label_visibility="collapsed",
        )

    with col_branch:
        st.text_input(
            "ブランチ（任意）",
            placeholder="例: main",
            key="repo_branch_input",
            label_visibility="collapsed",
            disabled=is_org,
        )

    with col_btn:
        submitted = st.button("登録", type="primary", use_container_width=True, key="repo_register_btn")

    if not submitted:
        return

    raw = raw.strip()
    if not raw:
        st.warning("対象を入力してください。")
        return

    if is_org:
        owner, repo = raw, ""
    else:
        parts = raw.split("/")
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            st.error("「owner/repo」の形式で入力してください。（例: my-org/my-repo）")
            return
        owner, repo = parts[0].strip(), parts[1].strip()

    try:
        repo_id      = services.get_or_create_repository(owner, repo)
        display_name = owner if is_org else f"{owner}/{repo}"
        # 登録成功 → 「今すぐ収集」待ち状態をセット
        st.session_state[_PENDING_COLLECT] = {
            "repo_id":      str(repo_id),
            "display_name": display_name,
            "kind":         "org" if is_org else "repo",
            "owner":        owner,
        }
        _set_flash("success", f"✅ `{display_name}` を登録しました。")
        st.rerun()
    except Exception as exc:
        st.error(f"登録に失敗しました: {exc}")


def _render_repo_table() -> None:
    repos = services.list_repositories()

    if not repos:
        st.info("登録済みリポジトリがありません。上のフォームから追加してください。")
        return

    st.markdown(f"**登録済み（{len(repos)} 件）**")
    st.write("")

    header_cols = st.columns(_COL_WIDTHS)
    for col, label in zip(header_cols, _COL_HEADERS):
        col.markdown(
            f'<span style="color:#59636e;font-size:12px;font-weight:600">{label}</span>',
            unsafe_allow_html=True,
        )

    for r in repos:
        owner       = str(r["owner"])
        repo        = str(r["repo"])
        last_at     = r["last_collected_at"]
        skill_count = int(str(r["skill_count"]))
        repo_id     = str(r["id"])

        kind         = _kind(repo)
        display_name = owner if kind == "org" else f"{owner}/{repo}"
        last_str     = last_at.strftime("%Y-%m-%d %H:%M") if last_at else "未収集"
        branch_str   = "—" if kind == "org" else "main"
        badge        = _KIND_ORG_BADGE if kind == "org" else _KIND_REPO_BADGE
        status_html  = (
            '<span style="color:#1f883d;font-weight:600">● 正常</span>'
            if last_at
            else '<span style="color:#59636e">○ 未収集</span>'
        )

        clicked = False
        with st.container(border=True):
            row = st.columns(_COL_WIDTHS)
            row[0].markdown(f"**{display_name}**")
            row[1].markdown(badge, unsafe_allow_html=True)
            row[2].caption(branch_str)
            row[3].caption(last_str)
            row[4].caption(str(skill_count))
            row[5].markdown(status_html, unsafe_allow_html=True)
            clicked = row[6].button("今すぐ収集", key=f"collect_{repo_id}", use_container_width=True)

        if clicked:
            _collect(repo_id, display_name, kind, owner)


def render() -> None:
    _, main_col, _ = st.columns([0.5, 6, 0.5])

    with main_col:
        st.subheader("📦 同期元")
        st.caption("Organization 単位または個別 owner/repo を登録。司書が定期的に SKILL.md を収集します。")
        st.write("")

        _render_flash()

        with st.container(border=True):
            st.markdown("**新規登録**")
            st.write("")
            _render_register_form()

        st.write("")
        _render_repo_table()
