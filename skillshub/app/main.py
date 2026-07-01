from __future__ import annotations

from typing import Literal

import streamlit as st

from skillshub.app.views import dashboard, repos, search


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
        ("dashboard", "🏠 ダッシュボード"),
        ("search", "🔍 自然言語検索"),
        ("suggestions", "💡 提案レビュー"),
        ("repos", "📦 リポジトリ登録"),
    ]

    with st.sidebar:
        st.title("📚 SkillsHub")
        st.caption("司書エージェント 稼働中")
        st.divider()

        for key, label in nav_items:
            btn_type: Literal["primary", "secondary"] = (
                "primary" if st.session_state.current_view == key else "secondary"
            )
            if st.button(label, key=f"nav_{key}", use_container_width=True, type=btn_type):
                st.session_state.current_view = key
                st.rerun()


def _render_content() -> None:
    view: str = st.session_state.current_view

    if view == "dashboard":
        dashboard.render()
    elif view == "search":
        search.render()
    elif view == "detail":
        st.title("📄 Skill 詳細")
        st.info(f"詳細画面は準備中です（Issue #15）  ·  Skill ID: {st.session_state.selected_skill_id}")
        if st.button("← ダッシュボードに戻る"):
            st.session_state.current_view = "dashboard"
            st.rerun()
    elif view == "suggestions":
        st.title("💡 提案レビュー")
        st.info("提案レビュー画面は準備中です（Issue #15）")
    elif view == "repos":
        repos.render()
    else:
        st.session_state.current_view = "dashboard"
        dashboard.render()


st.set_page_config(page_title="SkillsHub", page_icon="📚", layout="wide")
_init_session_state()
_render_sidebar()
_render_content()
