from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

import streamlit as st

from skillshub.shared import services

_MSG_KEY = "repos_flash_message"
_GO_DASH_KEY = "repos_go_dashboard"
_PENDING_COLLECT = "repos_pending_collect"  # 登録直後の「今すぐ収集」待ち


@dataclass
class _SyncJob:
    """バックグラウンドスレッドで実行中/完了した収集ジョブ。"""

    repo_id: str
    display_name: str
    kind: str
    owner: str
    thread: threading.Thread | None = None
    result: object | None = None
    error: Exception | None = None


# st.session_state は別スレッドから安全に触れないため、ジョブ管理はモジュールレベルの
# 素の dict で行う（ハッカソン用の単一プロセス運用が前提）。
# collect_org/collect_repo は GitHub API 取得＋AI 埋め込みで数十秒〜それ以上ブロックする。
# これを Streamlit のスクリプト実行と同じスレッドで呼ぶと、ブロック中の接続切断・再接続時に
# 途中描画と再接続後の描画が混在して行が二重表示される不具合が起きるため、
# バックグラウンドスレッドで実行しポーリングで完了を待つ。
_SYNC_JOBS: dict[str, _SyncJob] = {}
# 「実行中か確認 → dict へ登録」を1操作として保護するロック。
# 別セッション（別タブ/同時クリック）が同じ repo_id に対してほぼ同時に「今すぐ収集」を
# 押すと、ロックなしでは両方が「未実行」と判定して収集処理を二重起動しうるため必須。
_SYNC_JOBS_LOCK = threading.Lock()

_KIND_ORG_BADGE = (
    '<span style="background:#fbefff;color:#8250df;font-size:11px;font-weight:600;'
    'padding:2px 8px;border-radius:2em;white-space:nowrap">Org</span>'
)
_KIND_REPO_BADGE = (
    '<span style="background:#ddf4ff;color:#0969da;font-size:11px;font-weight:600;'
    'padding:2px 8px;border-radius:2em;white-space:nowrap">repo</span>'
)

_COL_WIDTHS = [3.5, 1, 2, 0.8, 1.5, 1.2]
_COL_HEADERS = ["対象", "種別", "最終収集", "Skills", "状態", ""]


def _set_flash(level: str, text: str, go_dashboard: bool = False) -> None:
    st.session_state[_MSG_KEY] = {"level": level, "text": text}
    if go_dashboard:
        st.session_state[_GO_DASH_KEY] = True


def _collect_worker(job: _SyncJob) -> None:
    """バックグラウンドスレッドの実行本体。Streamlit API は一切呼ばない。"""
    try:
        if job.kind == "org":
            job.result = services.collect_org(job.owner)
        else:
            job.result = services.collect_repo(job.repo_id)
    except Exception as exc:  # noqa: BLE001 — スレッド側で捕捉し、完了後に UI 側で表示する
        job.error = exc


def _start_sync(repo_id: str, display_name: str, kind: str, owner: str) -> None:
    """収集をバックグラウンドスレッドで開始する（同じ対象が実行中なら何もしない）。"""
    with _SYNC_JOBS_LOCK:
        if repo_id in _SYNC_JOBS:
            return
        job = _SyncJob(repo_id=repo_id, display_name=display_name, kind=kind, owner=owner)
        job.thread = threading.Thread(target=_collect_worker, args=(job,), daemon=True)
        _SYNC_JOBS[repo_id] = job
        # dict 登録とスレッド起動を同じロック内で行う。ロックの外で start() すると、
        # 登録済みだが未起動（is_alive() が False）の一瞬を _sync_poller が「完了」と
        # 誤判定しうるため。
        job.thread.start()


def _finish_job(job: _SyncJob) -> None:
    """完了したジョブの結果からフラッシュメッセージをセットする。"""
    if job.error is not None:
        _set_flash("error", f"`{job.display_name}` の収集に失敗しました。\n{job.error}")
        return

    if job.kind == "org":
        result = job.result
        assert isinstance(result, services.OrgCollectResult)
        if result.failed_repos:
            _set_flash(
                "warning",
                f"`{job.owner}` の収集が一部失敗しました。"
                f"　取得: {result.collected_skills} 件　／　失敗: {', '.join(result.failed_repos)}",
                go_dashboard=True,
            )
        else:
            _set_flash(
                "success",
                f"`{job.owner}` Org の収集が完了しました。"
                f"　取得: {result.collected_skills} 件　／　スキップ: {result.skipped_skills} 件",
                go_dashboard=True,
            )
    else:
        result_repo = job.result
        assert isinstance(result_repo, dict)
        _set_flash(
            "success",
            f"`{job.display_name}` の収集が完了しました。"
            f"　取得: {result_repo['collected_skills']} 件　／　スキップ: {result_repo['skipped_skills']} 件",
            go_dashboard=True,
        )


@st.fragment(run_every="1.5s")
def _sync_poller() -> None:
    """バックグラウンド収集ジョブの完了を監視し、完了したら全体を再描画する。

    このフラグメント自体は何も描画しない（対象行のボタン表示だけで状態を示す）。
    """
    with _SYNC_JOBS_LOCK:
        finished = [job for job in _SYNC_JOBS.values() if job.thread is not None and not job.thread.is_alive()]
        for job in finished:
            _SYNC_JOBS.pop(job.repo_id, None)
    for job in finished:
        _finish_job(job)
    if finished:
        st.rerun()


def _render_flash() -> None:
    # メッセージは1度だけ表示して消す
    msg = st.session_state.pop(_MSG_KEY, None)
    if msg is not None:
        getattr(st, msg["level"])(msg["text"])

    # 登録直後: 「今すぐ収集」ボタンをクリックされるまで表示し続ける
    pending = st.session_state.get(_PENDING_COLLECT)
    if pending:
        col_btn, col_skip, _ = st.columns([1.5, 1, 5])
        if col_btn.button("今すぐ収集", type="primary", key="collect_after_register_btn"):
            st.session_state.pop(_PENDING_COLLECT, None)
            _start_sync(pending["repo_id"], pending["display_name"], pending["kind"], pending["owner"])
            st.rerun()
        if col_skip.button("あとで", key="skip_collect_btn"):
            st.session_state.pop(_PENDING_COLLECT, None)
            st.rerun()

    # 収集完了後: 「ダッシュボードで確認」ボタンをクリックされるまで表示し続ける
    if st.session_state.get(_GO_DASH_KEY) and st.button("ダッシュボードで確認 →", type="primary", key="go_dash_btn"):
        st.session_state.pop(_GO_DASH_KEY, None)
        st.session_state.current_view = "dashboard"
        st.rerun()


def _kind(repo: str) -> str:
    return "org" if not repo else "repo"


@st.cache_data(ttl=300, show_spinner="エージェントの閲覧範囲を取得中…")
def _github_scope() -> dict[str, list[str]]:
    """App の閲覧範囲（選択肢）。GitHub API を毎リロードで叩かないよう短時間キャッシュする。

    ``persist="disk"`` は併用しない。Streamlit は persist 指定時に ttl を無視するため、
    キャッシュが永久に残り、新しく作られたリポジトリが候補に出てこなくなる。
    代償として起動直後の初回表示は取得スピナー（数秒）でブロックされる。
    """
    return services.list_github_scope()


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
        repo_id = services.get_or_create_repository(owner, repo)
        display_name = owner if is_org else f"{owner}/{repo}"
        # 登録成功 → 「今すぐ収集」待ち状態をセット
        st.session_state[_PENDING_COLLECT] = {
            "repo_id": str(repo_id),
            "display_name": display_name,
            "kind": "org" if is_org else "repo",
            "owner": owner,
        }
        _set_flash("success", f"`{display_name}` を登録しました。")
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

    header_cols = st.columns(_COL_WIDTHS)
    for col, label in zip(header_cols, _COL_HEADERS, strict=False):
        col.markdown(
            f'<span style="color:#59636e;font-size:12px;font-weight:600">{label}</span>',
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

        is_syncing_row = repo_id in _SYNC_JOBS

        clicked = False
        with st.container(border=True, key=f"repo_box_{repo_id}"):
            last_str = last_at.strftime("%Y-%m-%d %H:%M") if last_at else "未収集"
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
            # 擬似 owner（local samples / 手動登録の置き場）は GitHub に実在しないため
            # 「今すぐ収集」を出さない（collect_repo が installation 取得の 404 で落ちる）。
            if owner not in services.PSEUDO_OWNERS:
                if is_syncing_row:
                    row[5].button(
                        "同期中...",
                        key=f"collect_{repo_id}",
                        use_container_width=True,
                        disabled=True,
                        icon=":material/progress_activity:",
                    )
                else:
                    clicked = row[5].button("今すぐ収集", key=f"collect_{repo_id}", use_container_width=True)
            else:
                row[5].caption("収集対象外")

        if clicked:
            _start_sync(repo_id, display_name, kind, owner)
            st.rerun()


def render() -> None:
    _, main_col, _ = st.columns([0.5, 6, 0.5])

    with main_col:
        st.caption("Organization 単位または個別 owner/repo を登録。司書が定期的に SKILL.md を収集します。")
        st.write("")

        _render_flash()

        with st.container(border=True):
            st.markdown("**新規登録**")
            st.write("")
            _render_register_form()

        st.write("")
        _render_repo_table()

        # 実行中のジョブがあるときだけポーリングする（無いときは無駄な自動再実行をしない）。
        if _SYNC_JOBS:
            _sync_poller()
