from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

import streamlit as st

from skillshub.app.views import dashboard, detail, repos, search, suggestions
from skillshub.app.views.components import inject_github_style
from skillshub.shared import services

_SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "samples"


def _init_session_state() -> None:
    defaults: dict[str, object] = {
        "current_view": "dashboard",
        "selected_skill_id": None,
        "chat_history": [],
        "filters": {
            "keyword": "",
            "update_status": "",
            "tags": [],
            "sort_by": "updated",
        },
        "pending_search_query": "",
        "accepted_compose_suggestion": None,
        "saved_compose_keys": set(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _render_sidebar() -> None:
    nav_items = [
        ("dashboard", "スキル一覧"),
        ("search", "スキルを探す"),
        ("suggestions", "提案を確認する"),
        ("repos", "収集元を追加する"),
    ]

    with st.sidebar:
        st.markdown('<div class="sh-logo"><span class="sh-mark">S</span>SkillsHub</div>', unsafe_allow_html=True)
        st.divider()

        for key, label in nav_items:
            btn_type: Literal["primary", "secondary"] = (
                "primary" if st.session_state.current_view == key else "secondary"
            )
            if st.button(label, key=f"nav_{key}", use_container_width=True, type=btn_type):
                st.session_state.current_view = key
                st.rerun()

        st.divider()
        _render_agent_panel()


def _render_agent_panel() -> None:
    """同期の実行と最終同期時刻の表示（デモのヘッダー右側に相当）。"""
    repositories = services.list_repositories()
    last_times = [t for r in repositories if isinstance(t := r["last_collected_at"], datetime)]
    last_label = max(last_times).strftime("%Y-%m-%d %H:%M") if last_times else "未同期"

    if st.button("今すぐ同期", key="sync_all", use_container_width=True):
        _run_sync(repositories)
    st.markdown(f'<div class="sh-last">最終同期 {last_label}</div>', unsafe_allow_html=True)


def _run_sync(repositories: list[dict[str, object]]) -> None:
    """登録済みリポジトリを順に収集する。1 件の失敗で残りを止めない。"""
    if not repositories:
        st.toast("同期元がありません。「リポジトリ登録」から追加してください")
        return

    ok = 0
    failed = 0
    with st.status("エージェントが同期中…", expanded=True) as status:
        for r in repositories:
            name = f"{r['owner']}/{r['repo']}"
            # 擬似 owner（services.PSEUDO_OWNERS）のうち local はローカル samples を収集し、
            # それ以外（手動登録 Skill の置き場）は GitHub に実在しないため同期しない。
            if r["owner"] != services.LOCAL_OWNER and r["owner"] in services.PSEUDO_OWNERS:
                st.write(f"{name} は手動登録 Skill の置き場のためスキップしました")
                continue
            st.write(f"{name} を収集しています…")
            try:
                if r["owner"] == services.LOCAL_OWNER:
                    services.collect_local(_SAMPLES_ROOT)
                else:
                    services.collect_repo(str(r["id"]))
                ok += 1
            except Exception as exc:  # noqa: BLE001 — 1 リポジトリの失敗で他リポジトリを止めない
                failed += 1
                st.write(f"{name} の収集に失敗しました: {exc}")
        status.update(
            label=f"同期完了 — 成功 {ok} 件 / 失敗 {failed} 件",
            state="error" if failed else "complete",
            expanded=failed > 0,
        )
    st.toast(f"同期が完了しました（成功 {ok} / 失敗 {failed}）", icon="✅")


def _render_content() -> None:
    view: str = st.session_state.current_view

    if view == "dashboard":
        dashboard.render()
    elif view == "search":
        search.render()
    elif view == "detail":
        detail.render()
    elif view == "suggestions":
        suggestions.render()
    elif view == "repos":
        repos.render()
    else:
        st.session_state.current_view = "dashboard"
        dashboard.render()


st.set_page_config(page_title="SkillsHub", page_icon="📚", layout="wide")
inject_github_style()
_init_session_state()
_render_sidebar()
_render_content()
