from __future__ import annotations

import html

import streamlit as st

from skillshub.app.views.components import navigate_to_detail, tag_chip, update_status_badge
from skillshub.shared import services
from skillshub.shared.schemas import Skill

_UPDATE_STATUS_OPTIONS: dict[str, str] = {
    "": "状態: すべて",
    "current": "最新",
    "stale": "長期未更新",
    "needs_update": "要更新",
}


def _render_agent_bar() -> None:
    st.markdown('<div class="sh-ai-eyebrow">✦ エージェントに聞く</div>', unsafe_allow_html=True)
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
        st.metric("長期未更新", summary.stale_count)


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
                "状態",
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


def _render_skill_cards(skills: list[Skill]) -> None:
    if not skills:
        st.info("条件に一致する Skill が見つかりませんでした。")
        return

    st.caption(f"{len(skills)} 件")

    cols = st.columns(3, gap="medium")
    for i, skill in enumerate(skills):
        with cols[i % 3], st.container(border=True, key=f"skill_box_{skill.id}"):
            # ヘッダー行: スキル名（主役）。状態バッジは CSS でカード右上に絶対配置し、
            # 名前と同じ高さに固定する（カラムの縦センターは要素ごとの余白でずれるため）。
            if st.button(
                skill.name,
                key=f"skill_card_{skill.id}",
                use_container_width=True,
                help="クリックして詳細を表示",
            ):
                navigate_to_detail(str(skill.id))
            st.markdown(
                f'<div class="sh-badge-abs">{update_status_badge(skill.update_status)}</div>',
                unsafe_allow_html=True,
            )

            desc = skill.description
            if len(desc) > 90:
                desc = desc[:90] + "…"
            st.caption(desc)

            # フッター行: タグ（最大3個＋残数）とスキルの出所（作者）を1行にまとめる。
            shown_tags = skill.tags[:3]
            tags_html = " ".join(tag_chip(t) for t in shown_tags)
            rest = len(skill.tags) - len(shown_tags)
            if rest > 0:
                tags_html += f' <span style="font-size:11px;color:#8c959f">+{rest}</span>'
            author = html.escape(skill.author or "不明")
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'gap:8px;margin:4px 0 2px">'
                f'<span style="white-space:nowrap;overflow:hidden">{tags_html}</span>'
                f'<span style="font-size:12px;color:#59636e;white-space:nowrap">@{author}</span></div>',
                unsafe_allow_html=True,
            )


def render() -> None:
    _render_agent_bar()
    st.divider()
    _render_summary_cards()
    st.divider()

    all_tags = services.list_all_tags()
    keyword, update_status, tags, sort_by = _render_filters(all_tags)
    filtered_skills = services.list_skills(keyword=keyword, update_status=update_status, tags=tags, sort_by=sort_by)
    st.markdown("<br>", unsafe_allow_html=True)
    _render_skill_cards(filtered_skills)
