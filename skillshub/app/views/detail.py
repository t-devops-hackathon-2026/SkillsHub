"""Skill 詳細画面（ヘッダ・使い方・未対応提案の採用/却下・GitHub への誘導）。

品質スコア内訳・利用状況は Step3 スコープのため表示しない（step1.md）。
提案カードの描画は提案レビュー画面（suggestions.py）と共有する。
"""

from __future__ import annotations

import html
from datetime import datetime

import streamlit as st

from skillshub.app.views.components import tag_chip, to_jst, update_status_badge
from skillshub.app.views.suggestions import render_suggestion_card
from skillshub.shared import services
from skillshub.shared.schemas import SkillDetail


def _back_to_dashboard() -> None:
    st.session_state.selected_skill_id = None
    st.session_state.current_view = "dashboard"
    st.rerun()


def _source_file_url(detail: SkillDetail) -> str | None:
    """取得元 SKILL.md への GitHub リンク。HEAD は既定ブランチに解決される（er.md）。

    擬似 owner（local samples / 手動登録の置き場）は GitHub 上に実体が無いためリンクを出さない。
    """
    if detail.repo_owner in services.PSEUDO_OWNERS:
        return None
    return f"https://github.com/{detail.repo_owner}/{detail.repo_name}/blob/HEAD/{detail.skill.source_path}"


def _issues_url(detail: SkillDetail) -> str | None:
    if detail.repo_owner in services.PSEUDO_OWNERS:
        return None
    return f"https://github.com/{detail.repo_owner}/{detail.repo_name}/issues"


def _render_header(detail: SkillDetail) -> None:
    skill = detail.skill

    st.markdown(
        f'<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">'
        f'<span style="font-size:1.6rem;font-weight:600;color:#1f2328">{html.escape(skill.name)}</span>'
        f"{update_status_badge(skill.update_status)}</div>",
        unsafe_allow_html=True,
    )
    if skill.tags:
        st.markdown(" ".join(tag_chip(t) for t in skill.tags), unsafe_allow_html=True)
    if skill.description:
        st.markdown(skill.description)

    source_label = f"{detail.repo_owner}/{detail.repo_name}"
    source_url = _source_file_url(detail)
    source_html = (
        f'<a href="{source_url}" target="_blank">{html.escape(source_label)}</a>'
        if source_url
        else html.escape(source_label)
    )
    last_updated: datetime | None = skill.last_updated or skill.updated_at
    updated_label = to_jst(last_updated).strftime("%Y-%m-%d") if last_updated else "不明"
    author = html.escape(skill.author or "不明")
    st.markdown(
        f'<div style="font-size:13px;color:#59636e;display:flex;gap:16px;flex-wrap:wrap">'
        f"<span>作者 @{author}</span>"
        f"<span>取得元 {source_html}</span>"
        f"<span>最終更新 {updated_label}</span></div>",
        unsafe_allow_html=True,
    )


def _render_usage(detail: SkillDetail) -> None:
    st.subheader("使い方")
    st.caption("エージェントが SKILL.md から自動生成した利用例です。")
    if detail.skill.usage:
        st.markdown(detail.skill.usage)
    else:
        st.info("使い方はまだ生成されていません。")


def _render_suggestions(detail: SkillDetail) -> None:
    st.subheader("この Skill への提案")
    if not detail.open_suggestions:
        st.caption("未対応の提案はありません。")
        return
    for suggestion in detail.open_suggestions:
        # この Skill のページ内なので対象 Skill へのリンクは出さない。
        render_suggestion_card(suggestion, key_prefix=f"detail_{suggestion.id}", show_target_links=False)


def _render_discussion(detail: SkillDetail) -> None:
    st.subheader("議論・要望")
    issues_url = _issues_url(detail)
    if issues_url:
        st.markdown(f"この Skill への要望・議論は、取得元リポジトリの [GitHub Issues]({issues_url}) へどうぞ。")
    else:
        st.caption("GitHub 上に取得元が無い Skill のため、議論の場はありません。")


def render() -> None:
    if st.button("← 一覧に戻る", key="detail_back"):
        _back_to_dashboard()

    skill_id = st.session_state.selected_skill_id
    detail = services.get_skill(str(skill_id)) if skill_id else None
    if detail is None:
        st.warning("この Skill は見つかりませんでした。一覧から選び直してください。")
        return

    _render_header(detail)
    st.divider()
    _render_usage(detail)
    st.divider()
    _render_suggestions(detail)
    st.divider()
    _render_discussion(detail)
