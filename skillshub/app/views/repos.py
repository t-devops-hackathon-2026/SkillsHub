from __future__ import annotations

import time
from datetime import datetime

import streamlit as st

from skillshub.app.views.components import to_jst
from skillshub.shared import services

_MSG_KEY = "repos_flash_message"

_KIND_ORG_BADGE = (
    '<span style="background:#fbefff;color:#8250df;font-size:11px;font-weight:600;'
    'padding:2px 8px;border-radius:2em;white-space:nowrap">Org</span>'
)
_KIND_REPO_BADGE = (
    '<span style="background:#ddf4ff;color:#0969da;font-size:11px;font-weight:600;'
    'padding:2px 8px;border-radius:2em;white-space:nowrap">repo</span>'
)

_COL_WIDTHS = [3.5, 1, 2, 0.8, 1.5]
_COL_HEADERS = ["対象", "種別", "最終収集", "Skills 数", "状態"]


def _set_flash(level: str, text: str) -> None:
    st.session_state[_MSG_KEY] = {"level": level, "text": text}


def _render_flash() -> None:
    # メッセージは1度だけ表示して消す
    msg = st.session_state.pop(_MSG_KEY, None)
    if msg is not None:
        getattr(st, msg["level"])(msg["text"])


def _kind(repo: str) -> str:
    return "org" if not repo else "repo"


_SCOPE_TTL_SECONDS = 300
# _github_scope の中身（GitHub API 取得）が最後に実行された時刻。キャッシュが冷えている
# （＝次の取得が数秒ブロックする）かを cache_data の外から判定し、冷えている時だけ
# ローディングオーバーレイを出すために持つ。
_scope_warmed_at = 0.0

_LOADING_OVERLAY_HTML = (
    '<div class="sh-loading-overlay"><div class="sh-loading-panel">'
    '<div class="sh-loading-spinner"></div>'
    '<div class="sh-loading-text">エージェントの閲覧範囲を取得中…</div>'
    "</div></div>"
)


@st.cache_data(ttl=_SCOPE_TTL_SECONDS, show_spinner=False)
def _github_scope() -> dict[str, list[str]]:
    """App の閲覧範囲（選択肢）。GitHub API を毎リロードで叩かないよう短時間キャッシュする。

    ``persist="disk"`` は併用しない。Streamlit は persist 指定時に ttl を無視するため、
    キャッシュが永久に残り、新しく作られたリポジトリが候補に出てこなくなる。
    代償として初回表示は取得（数秒）でブロックされる。この間は render() が全画面の
    ローディングオーバーレイを出し、前画面の stale 要素が透けて見えないようにする。
    """
    global _scope_warmed_at
    scope = services.list_github_scope()
    _scope_warmed_at = time.time()
    return scope


def _scope_cache_cold() -> bool:
    """スコープのキャッシュが切れていて、次の取得が数秒ブロックしそうか。"""
    return time.time() - _scope_warmed_at >= _SCOPE_TTL_SECONDS


def _load_scope() -> dict[str, list[str]] | None:
    """登録フォームの選択肢を返す。取得できない場合は None（手入力にフォールバック）。"""
    if not services.github_app_configured():
        st.caption(
            "GitHub App の認証情報が未設定のため手入力で登録します。"
            "環境変数 GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY(_PATH) を設定すると、"
            "エージェントが閲覧できる範囲から選べるようになります。"
        )
        return None
    try:
        return _github_scope()
    except Exception as exc:
        st.warning(f"エージェントの閲覧範囲を取得できなかったため、手入力で登録します。（{exc}）")
        return None


def _render_register_form() -> None:
    scope = _load_scope()

    col_kind, col_name, col_btn = st.columns([1.8, 4.5, 1])

    with col_kind:
        kind = st.selectbox(
            "種別",
            ["owner/repo", "Organization"],
            key="repo_kind_select",
            label_visibility="collapsed",
        )

    is_org = kind == "Organization"

    with col_name:
        if scope is None:
            raw = st.text_input(
                "対象",
                placeholder="例: example-corp（配下の全リポジトリが対象）" if is_org else "例: owner/repo",
                key="repo_name_input",
                label_visibility="collapsed",
            )
        else:
            # 登録済みの対象（Org / owner/repo、Org 登録済み owner の配下リポジトリ含む）は
            # 候補から外す（登録自体は冪等だが、選択肢に出続けると二重登録に見える）。
            registered = services.list_repositories()
            registered_orgs = {str(r["owner"]) for r in registered if not str(r["repo"])}
            registered_repos = {f"{r['owner']}/{r['repo']}" for r in registered if str(r["repo"])}
            if is_org:
                candidates = sorted(scope)
                options = [o for o in candidates if o not in registered_orgs]
            else:
                candidates = sorted(r for repos in scope.values() for r in repos)
                options = [
                    r for r in candidates if r not in registered_repos and r.split("/", 1)[0] not in registered_orgs
                ]
            if not options:
                if candidates:
                    st.info("エージェントが閲覧できる対象は、すべて登録済みです。")
                else:
                    st.info("エージェントが閲覧できる対象がありません。GitHub App のインストール先を確認してください。")
                return
            selected = st.selectbox(
                "対象",
                options,
                index=None,
                placeholder="Organization を選択（配下の全リポジトリが対象）" if is_org else "リポジトリを選択",
                key="repo_org_select" if is_org else "repo_repo_select",
                label_visibility="collapsed",
            )
            raw = selected or ""

    with col_btn:
        submitted = st.button("登録", type="primary", use_container_width=True, key="repo_register_btn")

    if not submitted:
        return

    raw = raw.strip()
    if not raw:
        st.warning("対象を選択してください。" if scope is not None else "対象を入力してください。")
        return

    if is_org:
        if "/" in raw:
            st.error("Organization 名のみを入力してください。（例: my-org）")
            return
        owner, repo = raw, ""
    else:
        parts = raw.split("/")
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            st.error("「owner/repo」の形式で入力してください。（例: my-org/my-repo）")
            return
        owner, repo = parts[0].strip(), parts[1].strip()

    try:
        services.get_or_create_repository(owner, repo)
        display_name = owner if is_org else f"{owner}/{repo}"
        # 収集はサイドバーの「今すぐ同期」または司書の定期バッチから実行する
        _set_flash("success", f"**{display_name}** を登録しました。")
        st.rerun()
    except Exception as exc:
        st.error(f"登録に失敗しました: {exc}")


def _org_rollup(repos: list[dict[str, object]]) -> tuple[dict[str, datetime], dict[str, int]]:
    """Org 行（repo=""）の表示用に、同一 owner 配下リポジトリの最終収集・Skill 数を集計する。

    Org 行自体は登録マーカーで収集記録を持たないため、配下の実リポジトリから導出する。
    """
    last_by_owner: dict[str, datetime] = {}
    skills_by_owner: dict[str, int] = {}
    for r in repos:
        if not str(r["repo"]):
            continue
        owner = str(r["owner"])
        skills_by_owner[owner] = skills_by_owner.get(owner, 0) + int(str(r["skill_count"]))
        last_at = r["last_collected_at"]
        if isinstance(last_at, datetime) and (owner not in last_by_owner or last_at > last_by_owner[owner]):
            last_by_owner[owner] = last_at
    return last_by_owner, skills_by_owner


def _render_repo_table() -> None:
    repos = services.list_repositories()

    if not repos:
        st.info("登録済みリポジトリがありません。上のフォームから追加してください。")
        return

    st.markdown(f"**登録済み（{len(repos)} 件）**")
    st.write("")

    # データ行はカード（border 1px + padding 1rem）の中に描画されるため、ヘッダー行にも
    # 同じ水平オフセットを CSS（st-key-repo_table_header）で与えて桁位置を揃える。
    with st.container(key="repo_table_header"):
        header_cols = st.columns(_COL_WIDTHS)
        for col, label in zip(header_cols, _COL_HEADERS, strict=False):
            col.markdown(
                f'<span style="color:#59636e;font-size:12px;font-weight:600;white-space:nowrap">{label}</span>',
                unsafe_allow_html=True,
            )

    last_by_owner, skills_by_owner = _org_rollup(repos)

    for r in repos:
        owner = str(r["owner"])
        repo = str(r["repo"])
        repo_id = str(r["id"])

        kind = _kind(repo)
        if kind == "org":
            display_name = owner
            last_at = last_by_owner.get(owner)
            skill_count = skills_by_owner.get(owner, 0)
        else:
            display_name = f"{owner}/{repo}"
            last_at = r["last_collected_at"] if isinstance(r["last_collected_at"], datetime) else None
            skill_count = int(str(r["skill_count"]))

        with st.container(border=True, key=f"repo_box_{repo_id}"):
            last_str = to_jst(last_at).strftime("%Y-%m-%d %H:%M") if last_at else "未収集"
            badge = _KIND_ORG_BADGE if kind == "org" else _KIND_REPO_BADGE
            status_html = (
                '<span style="color:#1f883d;font-weight:600">● 正常</span>'
                if last_at
                else '<span style="color:#59636e">○ 未収集</span>'
            )

            row = st.columns(_COL_WIDTHS)
            row[0].markdown(f"**{display_name}**")
            row[1].markdown(badge, unsafe_allow_html=True)
            row[2].caption(last_str)
            row[3].caption(str(skill_count))
            row[4].markdown(status_html, unsafe_allow_html=True)


def render() -> None:
    _, main_col, _ = st.columns([0.5, 6, 0.5])

    with main_col:
        st.caption("Organization 単位または個別 owner/repo を登録。司書が定期的に SKILL.md を収集します。")
        st.write("")

        _render_flash()

        # スコープ取得で数秒ブロックする間、前画面の stale 要素が透けて見えると
        # バグのように映るため、メイン領域をオーバーレイで覆って読み込み中を明示する
        # （サイドバーは見せたまま残す）。キャッシュが温かい時は出さず、ちらつきを避ける。
        overlay = st.empty()
        if services.github_app_configured() and _scope_cache_cold():
            overlay.markdown(_LOADING_OVERLAY_HTML, unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown("**新規登録**")
            st.write("")
            _render_register_form()
        overlay.empty()

        st.write("")
        _render_repo_table()
