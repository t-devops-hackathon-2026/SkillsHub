from __future__ import annotations

import html
import time

import streamlit as st

from skillshub.app.views.components import navigate_to_detail, update_status_badge
from skillshub.shared import services
from skillshub.shared.schemas import (
    ComposeSuggestion,
    SearchResult,
    SearchResultItem,
)

# エージェントの「途中表示」段階。デモ映え用に解析→横断検索→照合の3段で見せる。
_SEARCH_STEPS: tuple[str, ...] = (
    "要求を解析しています…",
    "Skills を横断検索しています…",
    "鮮度を照合しています…",
)
# 各段の待機時間（秒）。体験の見せ場なので少しだけ溜める。
_STEP_DELAY: float = 0.6

_GREETING: str = (
    "やりたいことを文章で教えてください。"
    "社内に散らばった Skills を横断検索して、最適な候補を提案します。"
    "（例: 議事録を要約したい）"
)


def _seed_greeting() -> None:
    if not st.session_state.chat_history:
        st.session_state.chat_history.append({"role": "assistant", "content": _GREETING})


def _accept_compose(compose: ComposeSuggestion, save_key: str) -> None:
    # 採用された合成提案を Suggestion(type=compose) として保存し（#17 register_compose_suggestion）、
    # 提案レビュー画面（#20）へ誘導する。保存失敗時はアプリを落とさず通知して同じ画面に留まる。
    try:
        services.register_compose_suggestion(compose)
    except Exception:  # noqa: BLE001 — DB 保存失敗でも検索体験は壊さず、ユーザーに通知して留まる
        st.error("合成提案の保存に失敗しました。時間をおいて再度お試しください。")
        return

    # 同じ提案カードからの二重保存を防ぐため、保存済みキーを控える（再描画時はボタンを出さない）。
    st.session_state.saved_compose_keys.add(save_key)
    st.session_state.accepted_compose_suggestion = compose
    st.session_state.current_view = "suggestions"
    st.toast("合成提案を保存しました", icon="✅")
    st.rerun()


def _render_item_card(item: SearchResultItem, key_prefix: str) -> None:
    skill = item.skill
    with st.container(border=True, key=f"{key_prefix}_box"):
        # ダッシュボードのカードと同じ「スキル名＋右上バッジ」構成に揃える。
        if st.button(
            skill.name,
            key=f"{key_prefix}_card_{skill.id}",
            use_container_width=True,
            help="クリックして詳細を表示",
        ):
            navigate_to_detail(str(skill.id))
        st.markdown(
            f'<div class="sh-badge-abs">{update_status_badge(skill.update_status)}</div>',
            unsafe_allow_html=True,
        )

        st.caption(skill.description)
        st.progress(item.confidence, text=f"確信度 {round(item.confidence * 100)}%")
        st.markdown(
            f'<div style="font-size:13px;color:#59636e;border-top:1px dashed #d0d7de;'
            f'margin-top:8px;padding-top:7px">💡 推薦理由: {html.escape(item.reason)}</div>',
            unsafe_allow_html=True,
        )


def _render_compose(compose: ComposeSuggestion, key_prefix: str) -> None:
    with st.container(border=True, key=f"{key_prefix}_compose_box"):
        st.markdown(f"**合成ワークフローの提案** — {html.escape(compose.title)}")
        st.caption(compose.body)
        # 一度採用した提案カードは保存済み表示にして、再描画時の二重保存を防ぐ。
        if key_prefix in st.session_state.saved_compose_keys:
            st.success("保存済み（提案レビューで確認できます）")
            return
        if st.button(
            "この合成提案を採用",
            key=f"{key_prefix}_compose_accept",
            type="primary",
        ):
            _accept_compose(compose, save_key=key_prefix)


def _render_result(result: SearchResult, key_prefix: str) -> None:
    items = result.items[:3]
    if not items:
        st.markdown("該当する Skills が見つかりませんでした。別の言い方や、目的を具体的に書いていただけますか。")
        return

    top = round(items[0].confidence * 100)
    st.markdown(f"**{len(items)} 件の Skills が見つかりました**　·　確信度 {top}%")

    for i, item in enumerate(items):
        _render_item_card(item, key_prefix=f"{key_prefix}_{i}")

    # 候補が2件以上のときだけ合成ワークフローを併記する。
    if len(items) >= 2 and result.compose_suggestion is not None:
        _render_compose(result.compose_suggestion, key_prefix=key_prefix)


def _render_history() -> None:
    for idx, msg in enumerate(st.session_state.chat_history):
        with st.chat_message(msg["role"]):
            if "result" in msg:
                _render_result(msg["result"], key_prefix=f"hist_{idx}")
            else:
                st.markdown(msg["content"])


def _run_search(query: str) -> None:
    st.session_state.chat_history.append({"role": "user", "content": query})

    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        with st.status("考えています…", expanded=True) as status:
            for step in _SEARCH_STEPS:
                st.write(step)
                time.sleep(_STEP_DELAY)
            status.update(label="検索が完了しました", state="complete", expanded=False)

        result = services.search_skills(query)
        st.session_state.chat_history.append({"role": "assistant", "query": query, "result": result})
        _render_result(result, key_prefix=f"hist_{len(st.session_state.chat_history) - 1}")


def render() -> None:
    st.title("スキルを探す")
    st.caption("やりたいことを書くと、エージェントが社内のスキルから最適な候補を提案します。")
    _seed_greeting()
    _render_history()

    # ダッシュボードのエージェントバーから渡されたクエリを優先的に消化する。
    pending = st.session_state.pending_search_query
    if pending:
        st.session_state.pending_search_query = ""
        _run_search(pending)
        return

    prompt = st.chat_input("やりたいことを書いてください（例: 議事録を要約したい）")
    if prompt:
        _run_search(prompt)
