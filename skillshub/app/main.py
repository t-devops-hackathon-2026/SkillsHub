from __future__ import annotations

import hmac
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

import streamlit as st

from skillshub.app.views import dashboard, detail, repos, search, suggestions
from skillshub.app.views.components import inject_github_style
from skillshub.shared import services

_SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "samples"


def _check_password() -> bool:
    """公開デプロイ向けの簡易パスワードゲート。

    ``APP_PASSWORD`` が未設定（ローカル開発）ならゲートを出さず素通しする。
    デプロイ環境では Secret Manager の値を ``--set-secrets`` で env として渡す想定。
    """
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        return True
    if st.session_state.get("password_verified"):
        return True

    _, center, _ = st.columns([1, 1.1, 1])
    with center:
        st.markdown(
            '<div class="sh-login-head">'
            '<span class="sh-mark">S</span>'
            '<div class="sh-login-title">SkillsHub にログイン</div>'
            '<div class="sh-login-sub">パスワードを入力してください</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        # st.form 自体には key クラスが付かないため、CSS で狙えるよう keyed container で包む。
        with st.container(key="login_card"), st.form("password_gate"):
            entered = st.text_input("パスワード", type="password")
            submitted = st.form_submit_button("ログイン", type="primary", use_container_width=True)
        if submitted:
            # str のまま比較すると非 ASCII 入力で TypeError になるため bytes で比較する。
            if hmac.compare_digest(entered.encode(), expected.encode()):
                st.session_state.password_verified = True
                st.rerun()
            st.error("パスワードが違います")
    return False


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


def _navigate(view: str) -> None:
    """ナビの on_click。スクリプト再実行が始まる前に遷移先を確定させる。

    ``if st.button(...): st.rerun()`` 方式は「1回目の再実行を途中で捨てて2回目で新画面」
    という二重描画になり、遷移のたびに画面がちらつくため使わない。
    """
    st.session_state.current_view = view


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
            st.button(
                label,
                key=f"nav_{key}",
                use_container_width=True,
                type=btn_type,
                on_click=_navigate,
                args=(key,),
            )

        st.divider()
        _render_agent_panel()
        st.divider()
        _render_reset_panel()


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
        # Organization 登録（repo=""）を先に一括収集し、配下リポジトリの二重収集を避ける。
        synced_ids: set[str] = set()
        for r in repositories:
            owner = str(r["owner"])
            if r["repo"]:
                continue
            st.write(f"{owner}（Organization）を収集しています…")
            try:
                org_result = services.collect_org(owner)
                synced_ids.update(org_result.repo_ids)
                ok += len(org_result.repo_ids) - len(org_result.failed_repos)
                failed += len(org_result.failed_repos)
                for failed_name in org_result.failed_repos:
                    st.write(f"{failed_name} の収集に失敗しました")
            except Exception as exc:  # noqa: BLE001 — 1 Org の失敗で他の同期元を止めない
                failed += 1
                st.write(f"{owner} の収集に失敗しました: {exc}")

        for r in repositories:
            if not r["repo"] or str(r["id"]) in synced_ids:
                continue
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
    st.toast(f"同期が完了しました（成功 {ok} / 失敗 {failed}）", icon=":material/check_circle:")


def _render_reset_panel() -> None:
    """全データを seed 直後の状態に戻す危険操作（ハッカソンのデモやり直し用）。"""
    with (
        st.container(key="demo_reset_area"),
        st.popover("初期状態に戻す（ハッカソン用）", use_container_width=True),
    ):
        st.markdown("登録リポジトリ・収集済み Skill・提案・検索履歴をすべて削除し、デモ用の初期データに戻します。")
        st.caption("ハッカソンで同期の挙動を確認したい時に自由にお使いください。")
        if st.button("リセットを実行", key="demo_reset_confirm", use_container_width=True):
            services.reset_demo_data()
            st.cache_data.clear()
            st.session_state.clear()
            st.toast("初期状態に戻しました")
            st.rerun()


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


st.set_page_config(page_title="SkillsHub", page_icon=":material/local_library:", layout="wide")
inject_github_style()
if _check_password():
    _init_session_state()
    _render_sidebar()
    _render_content()
