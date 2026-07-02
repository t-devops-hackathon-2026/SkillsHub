"""提案レビュー画面（未対応の提案一覧と採用/却下）。

提案カードの描画（``render_suggestion_card``）は Skill 詳細画面（detail.py）とも共有する。
採用時の挙動（update は対象 Skill を「最新」に戻す等）はサービス層に委ねる
（step1.md「提案の採用時挙動」）。
"""

from __future__ import annotations

import streamlit as st

from skillshub.app.views.components import navigate_to_detail, suggestion_type_badge
from skillshub.shared import services
from skillshub.shared.schemas import SuggestionType, SuggestionView


def _accept(suggestion: SuggestionView) -> None:
    try:
        services.accept_suggestion(str(suggestion.id))
    except Exception:  # noqa: BLE001 — DB 更新失敗でも画面は壊さず、ユーザーに通知して留まる
        st.error("提案の採用に失敗しました。時間をおいて再度お試しください。")
        return
    if suggestion.type is SuggestionType.UPDATE:
        st.toast("提案を採用し、対象の Skill を「最新」に戻しました", icon="✅")
    else:
        st.toast("提案を採用しました", icon="✅")
    st.rerun()


def _dismiss(suggestion: SuggestionView) -> None:
    try:
        services.dismiss_suggestion(str(suggestion.id))
    except Exception:  # noqa: BLE001 — DB 更新失敗でも画面は壊さず、ユーザーに通知して留まる
        st.error("提案の却下に失敗しました。時間をおいて再度お試しください。")
        return
    st.toast("提案を却下しました")
    st.rerun()


def render_suggestion_card(suggestion: SuggestionView, key_prefix: str, *, show_target_links: bool = True) -> None:
    """提案 1 件をカードで描画する（バッジ・対象 Skill リンク・本文・採用/却下）。

    ``show_target_links=False`` は Skill 詳細画面用（その Skill のページ内なのでリンク不要）。
    """
    with st.container(border=True, key=f"suggestion_box_{key_prefix}"):
        st.markdown(
            f'<div class="sh-badge-abs">{suggestion_type_badge(suggestion.type)}</div>',
            unsafe_allow_html=True,
        )
        st.caption(f"{suggestion.created_at:%Y-%m-%d %H:%M} の提案")

        if show_target_links and suggestion.targets:
            cols = st.columns(3)
            for i, target in enumerate(suggestion.targets):
                with cols[i % 3]:
                    if st.button(
                        target.skill_name,
                        key=f"{key_prefix}_link_{target.skill_id}",
                        help="対象の Skill 詳細を表示",
                    ):
                        navigate_to_detail(str(target.skill_id))

        # update の content は diff 下書き（er.md: diff カラムは持たず content に内包）。
        if suggestion.type is SuggestionType.UPDATE:
            st.code(suggestion.content, language="diff")
        else:
            st.markdown(suggestion.content)

        col1, col2, _spacer = st.columns([1, 1, 4])
        with col1:
            if st.button("採用", key=f"{key_prefix}_accept", type="primary", use_container_width=True):
                _accept(suggestion)
        with col2:
            if st.button("却下", key=f"{key_prefix}_dismiss", use_container_width=True):
                _dismiss(suggestion)


def render() -> None:
    st.title("改善の提案を確認する")
    st.caption("エージェントが見つけた重複の統合・内容の更新・ワークフロー合成の提案を、採用または却下します。")

    # 検索画面で合成提案を採用した直後の遷移なら、保存されたことを一度だけ知らせる。
    accepted_compose = st.session_state.accepted_compose_suggestion
    if accepted_compose is not None:
        st.session_state.accepted_compose_suggestion = None
        st.success(f"合成提案「{accepted_compose.title}」を保存しました。下の一覧から確認できます。")

    suggestions = services.list_suggestions()
    if not suggestions:
        st.info("未対応の提案はありません。")
        return

    st.caption(f"{len(suggestions)} 件")
    for suggestion in suggestions:
        render_suggestion_card(suggestion, key_prefix=f"sugg_{suggestion.id}")
