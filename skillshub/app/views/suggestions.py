"""提案レビュー画面（未対応の提案一覧と採用/却下）。

提案カードの描画（``render_suggestion_card``）は Skill 詳細画面（detail.py）とも共有する。
採用時の挙動（update は対象 Skill を「最新」に戻す等）はサービス層に委ねる
（step1.md「提案の採用時挙動」）。
"""

from __future__ import annotations

import re

import streamlit as st

from skillshub.app.views.components import navigate_to_detail, suggestion_type_badge
from skillshub.shared import services
from skillshub.shared.schemas import SuggestionStatus, SuggestionType, SuggestionView


def _accept(suggestion: SuggestionView) -> None:
    try:
        services.accept_suggestion(str(suggestion.id))
    except Exception:  # noqa: BLE001 — DB 更新失敗でも画面は壊さず、ユーザーに通知して留まる
        st.error("記録に失敗しました。時間をおいて再度お試しください。")
        return
    if suggestion.type is SuggestionType.UPDATE:
        st.toast("対応することにしました。鮮度を「最新」に戻しました（SKILL.md への反映は手元で）", icon="✅")
    else:
        st.toast("対応することにしました（SKILL.md への反映は手元で）", icon="✅")
    st.rerun()


def _dismiss(suggestion: SuggestionView) -> None:
    try:
        services.dismiss_suggestion(str(suggestion.id))
    except Exception:  # noqa: BLE001 — DB 更新失敗でも画面は壊さず、ユーザーに通知して留まる
        st.error("記録に失敗しました。時間をおいて再度お試しください。")
        return
    st.toast("この提案は対応しないことにしました")
    st.rerun()


# update の content 内の diff は ```diff フェンスで内包される（analyzer.format_update_draft）。
_DIFF_FENCE = re.compile(r"```diff\n(.*?)```", re.DOTALL)


def _render_update_content(content: str) -> None:
    """update 提案の content を、散文（状況・提案）と diff に分けて描画する。

    フェンスが無い旧形式（diff 直書き）は従来どおり丸ごとコード表示にする。
    """
    matches = list(_DIFF_FENCE.finditer(content))
    if not matches:
        st.code(content, language="diff")
        return

    pos = 0
    for m in matches:
        prose = content[pos : m.start()].strip()
        if prose:
            st.markdown(prose)
        with st.expander("修正方針の diff を見る", expanded=False):
            st.code(m.group(1).rstrip(), language="diff")
        pos = m.end()
    rest = content[pos:].strip()
    if rest:
        st.markdown(rest)


def _render_compose_content(content: str) -> None:
    """compose 提案の content（1行目=タイトル、以降=本文）を描画する。"""
    title, _, body = content.partition("\n\n")
    st.markdown(f"**{title.strip()}**")
    if body.strip():
        st.markdown(body.strip())


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
            cols = st.columns([1, 3, 3, 3])
            with cols[0]:
                st.markdown(
                    '<div style="font-size:13px;color:#59636e;padding-top:2px">対象:</div>',
                    unsafe_allow_html=True,
                )
            for i, target in enumerate(suggestion.targets):
                with cols[1 + i % 3]:
                    if st.button(
                        target.skill_name,
                        key=f"{key_prefix}_link_{target.skill_id}",
                        help="対象の Skill 詳細を表示",
                    ):
                        navigate_to_detail(str(target.skill_id))

        if suggestion.type is SuggestionType.UPDATE:
            _render_update_content(suggestion.content)
        elif suggestion.type is SuggestionType.COMPOSE:
            _render_compose_content(suggestion.content)
        else:
            st.markdown(suggestion.content)

        # 未対応なら判断ボタン、判断済みなら結果を読み取り専用で示す。
        if suggestion.status is SuggestionStatus.OPEN:
            col1, col2, _spacer = st.columns([1.2, 1.2, 3.6])
            with col1:
                if st.button("対応する", key=f"{key_prefix}_accept", type="primary", use_container_width=True):
                    _accept(suggestion)
            with col2:
                if st.button("対応しない", key=f"{key_prefix}_dismiss", use_container_width=True):
                    _dismiss(suggestion)
        elif suggestion.status is SuggestionStatus.ACCEPTED:
            st.markdown(
                '<span style="color:#1a7f37;font-size:13px;font-weight:600">✓ 対応すると判断済み</span>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<span style="color:#59636e;font-size:13px;font-weight:600">対応しないと判断済み</span>',
                unsafe_allow_html=True,
            )


def _load_resolved() -> list[SuggestionView]:
    """判断済み（対応する / 対応しない）の提案を新しい順で返す。"""
    resolved = services.list_suggestions(SuggestionStatus.ACCEPTED) + services.list_suggestions(
        SuggestionStatus.DISMISSED
    )
    return sorted(resolved, key=lambda s: s.created_at, reverse=True)


def render() -> None:
    st.title("提案を確認する")
    st.caption("エージェントが見つけた重複の統合・内容の更新・ワークフロー合成の提案に、対応するかどうかを決めます。")

    # 検索画面で合成提案を採用した直後の遷移なら、保存されたことを一度だけ知らせる。
    accepted_compose = st.session_state.accepted_compose_suggestion
    if accepted_compose is not None:
        st.session_state.accepted_compose_suggestion = None
        st.success(f"合成提案「{accepted_compose.title}」を保存しました。下の一覧から確認できます。")

    mode = st.radio(
        "表示する提案",
        options=["未対応", "処理済み"],
        horizontal=True,
        label_visibility="collapsed",
        key="suggestions_mode",
    )

    if mode == "処理済み":
        suggestions = _load_resolved()
        empty_message = "処理済みの提案はまだありません。"
    else:
        suggestions = services.list_suggestions()
        empty_message = "未対応の提案はありません。"

    if not suggestions:
        st.info(empty_message)
        return

    st.caption(f"{len(suggestions)} 件")
    for suggestion in suggestions:
        render_suggestion_card(suggestion, key_prefix=f"sugg_{suggestion.id}")
