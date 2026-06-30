from __future__ import annotations

import html

import streamlit as st

from skillshub.shared import services
from skillshub.shared.schemas import Skill, UpdateStatus

_UPDATE_STATUS_CONFIG: dict[UpdateStatus, tuple[str, str, str]] = {
    UpdateStatus.CURRENT: ("最新", "#dafbe1", "#1a7f37"),
    UpdateStatus.STALE: ("要注意", "#fff8c5", "#9a6700"),
    UpdateStatus.NEEDS_UPDATE: ("要更新", "#ffebe9", "#cf222e"),
}

_UPDATE_STATUS_OPTIONS: dict[str, str] = {
    "": "鮮度: すべて",
    "current": "最新",
    "stale": "要注意",
    "needs_update": "要更新",
}


def _update_status_badge(status: UpdateStatus) -> str:
    label, bg, color = _UPDATE_STATUS_CONFIG[status]
    return (
        f'<span style="background:{bg};color:{color};padding:2px 10px;'
        f'border-radius:12px;font-size:12px;font-weight:600;display:inline-block">'
        f"{label}</span>"
    )


def _tag_chip(tag: str) -> str:
    safe = html.escape(tag)
    return (
        f'<span style="background:#ddf4ff;color:#0969da;padding:1px 8px;'
        f'border-radius:12px;font-size:12px">{safe}</span>'
    )


def _render_agent_bar() -> None:
    col1, col2 = st.columns([6, 1])
    with col1:
        query = st.text_input(
            "エージェントバー",
            placeholder="Skillsをここから探せます（例：議事録を要約して担当者別のタスクに分けたい）",
            label_visibility="collapsed",
            key="agent_bar_query",
        )
    with col2:
        if st.button("探す", use_container_width=True, key="agent_bar_submit") and query:
            st.session_state.pending_search_query = query
            st.session_state.current_view = "search"
            st.rerun()


def _render_summary_cards() -> None:
    summary = services.get_summary()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("登録 Skills 数", summary.total_skills)
    with c2:
        st.metric("重複候補", summary.duplicate_candidates)
    with c3:
        st.metric("要更新", summary.needs_update)
    with c4:
        st.metric("陳腐化注意", summary.stale_count)


_SORT_OPTIONS: dict[str, str] = {
    "updated": "更新日順",
}


def _render_filters(all_tags: list[str]) -> tuple[str, str, list[str], str]:
    col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
    with col1:
        keyword: str = st.text_input(
            "絞り込み",
            placeholder="名前・説明・タグで絞り込み",
            label_visibility="collapsed",
            key="filter_keyword",
        )
    with col2:
        update_status: str = (
            st.selectbox(
                "鮮度",
                options=list(_UPDATE_STATUS_OPTIONS.keys()),
                format_func=lambda x: _UPDATE_STATUS_OPTIONS.get(x, x),
                label_visibility="collapsed",
                key="filter_update_status",
            )
            or ""
        )
    with col3:
        selected_tags: list[str] = st.multiselect(
            "タグ",
            options=all_tags,
            placeholder="タグを選択",
            label_visibility="collapsed",
            key="filter_tags",
        )
    with col4:
        sort_by: str = (
            st.selectbox(
                "ソート",
                options=list(_SORT_OPTIONS.keys()),
                format_func=lambda x: _SORT_OPTIONS.get(x, x),
                label_visibility="collapsed",
                key="filter_sort",
            )
            or "updated"
        )

    return keyword, update_status, selected_tags, sort_by


def _navigate_to_detail(skill: Skill) -> None:
    st.session_state.selected_skill_id = str(skill.id)
    st.session_state.current_view = "detail"
    st.rerun()


def _render_skill_cards(skills: list[Skill]) -> None:
    if not skills:
        st.info("条件に一致する Skill が見つかりませんでした。")
        return

    st.caption(f"{len(skills)} 件")

    cols = st.columns(3, gap="medium")
    for i, skill in enumerate(skills):
        with cols[i % 3], st.container(border=True):
            st.markdown(_update_status_badge(skill.update_status), unsafe_allow_html=True)

            if st.button(
                skill.name,
                key=f"skill_card_{skill.id}",
                use_container_width=True,
                help="クリックして詳細を表示",
            ):
                _navigate_to_detail(skill)

            desc = skill.description
            if len(desc) > 90:
                desc = desc[:90] + "…"
            st.caption(desc)

            tags_html = " ".join(_tag_chip(t) for t in skill.tags)
            st.markdown(tags_html, unsafe_allow_html=True)

            author = html.escape(skill.author or "不明")
            updated = skill.last_updated.strftime("%Y-%m-%d") if skill.last_updated else "-"
            st.markdown(
                f'<p style="font-size:12px;color:#59636e;margin:6px 0 0">@{author} · 更新: {updated}</p>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p style="font-size:11px;color:#8c959f;margin:2px 0 0;font-family:monospace">'
                f"{html.escape(skill.source_path)}</p>",
                unsafe_allow_html=True,
            )


def render() -> None:
    st.subheader("ダッシュボード")

    _render_agent_bar()
    st.divider()
    _render_summary_cards()
    st.divider()

    all_tags = services.list_all_tags()
    keyword, update_status, tags, sort_by = _render_filters(all_tags)
    filtered_skills = services.list_skills(keyword=keyword, update_status=update_status, tags=tags, sort_by=sort_by)
    st.markdown("<br>", unsafe_allow_html=True)
    _render_skill_cards(filtered_skills)
