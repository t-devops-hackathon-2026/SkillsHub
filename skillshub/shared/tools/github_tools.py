"""GitHub App 経由で登録リポジトリから Skill（SKILL.md とその関連ファイル）を収集するツール。

仕様の正は overview.md「GitHub App 連携」。認証フローは以下の通り:

    App秘密鍵(Secret Manager) → JWT生成(RS256/10分) → Installation Access Token取得
    → 以降の API 呼び出し（リポジトリ列挙・ツリー取得・本文取得・コミットメタ取得）

差分検知のために SKILL.md ＋関連ファイル群をまとめた SHA-256（``content_hash``）を計算する。
``content_hash`` が前回と同一なら上位レイヤー（解析・LLM）をスキップできる。

CLI 動作確認:

    python -m skillshub.shared.tools.github_tools owner/repo   # 単一リポジトリを収集
    python -m skillshub.shared.tools.github_tools owner        # Org/インストール配下を全列挙
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

from skillshub.shared.config import get_secret

# ── 定数 ────────────────────────────────────────────────

GITHUB_API = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"

# JWT は最大10分まで有効。クロックスキュー対策で iat を 60 秒前倒しする。
_JWT_TTL = timedelta(minutes=9)
_JWT_CLOCK_SKEW = timedelta(seconds=60)

# レート対策: リトライ回数と指数バックオフの基準秒。
_MAX_RETRIES = 4
_BACKOFF_BASE = 1.5

# SKILL.md の検出はファイル名のみ大文字小文字許容で行う。
_SKILL_FILENAME = "skill.md"


# ── データ構造 ──────────────────────────────────────────


@dataclass(frozen=True)
class SkillFile:
    """Skill ディレクトリ配下の1ファイル。``content`` は生バイト列。"""

    path: str
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


@dataclass
class CollectedSkill:
    """1つの SKILL.md とその関連ファイル一式を1 Skill として表す。"""

    owner: str
    repo: str
    skill_dir: str  # SKILL.md が置かれたディレクトリ（リポジトリ root の場合は ""）
    skill_md_path: str  # 例: skills/foo/SKILL.md
    skill_md: SkillFile
    related_files: list[SkillFile] = field(default_factory=list)
    author: str | None = None
    last_commit_at: datetime | None = None
    content_hash: str = ""

    @property
    def source_path(self) -> str:
        """``repository`` 配下での SKILL.md のパス（DB の source_path 相当）。"""
        return self.skill_md_path

    @property
    def all_files(self) -> list[SkillFile]:
        return [self.skill_md, *self.related_files]


# ── 認証（App秘密鍵 → JWT → Installation Token）──────────


def _resolve_app_id() -> str:
    """App ID を取得する。ローカルは環境変数、本番は Secret Manager。"""
    return os.environ.get("GITHUB_APP_ID") or get_secret("github-app-id")


def _resolve_private_key() -> str:
    """App 秘密鍵(PEM)を取得する。ローカルは環境変数、本番は Secret Manager。"""
    return os.environ.get("GITHUB_APP_PRIVATE_KEY") or get_secret("github-app-private-key")


def generate_app_jwt(app_id: str | None = None, private_key: str | None = None) -> str:
    """GitHub App を認証するための JWT(RS256) を生成する。

    ``iss`` に App ID、有効期限は最大10分以内（ここでは9分）。クロックスキュー対策で
    ``iat`` を60秒前倒しする。
    """
    app_id = app_id or _resolve_app_id()
    private_key = private_key or _resolve_private_key()
    now = datetime.now(UTC)
    payload = {
        "iat": int((now - _JWT_CLOCK_SKEW).timestamp()),
        "exp": int((now + _JWT_TTL).timestamp()),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def _app_headers(app_jwt: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": _ACCEPT,
        "X-GitHub-Api-Version": _API_VERSION,
    }


def get_installation_id_for_repo(app_jwt: str, owner: str, repo: str) -> int:
    """対象リポジトリにアクセスできる installation_id を取得する。"""
    resp = _request("GET", f"{GITHUB_API}/repos/{owner}/{repo}/installation", headers=_app_headers(app_jwt))
    data: dict[str, Any] = resp.json()
    return int(data["id"])


def get_installation_id_for_org(app_jwt: str, org: str) -> int:
    """対象 Org にインストールされた installation_id を取得する。"""
    resp = _request("GET", f"{GITHUB_API}/orgs/{org}/installation", headers=_app_headers(app_jwt))
    data: dict[str, Any] = resp.json()
    return int(data["id"])


def get_installation_token(app_jwt: str, installation_id: int) -> str:
    """Installation Access Token を発行する（以降の API 呼び出しで使う短命トークン）。"""
    url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
    resp = _request("POST", url, headers=_app_headers(app_jwt))
    data: dict[str, Any] = resp.json()
    token: str = data["token"]
    return token


# ── HTTP（リトライ・指数バックオフ付き）────────────────


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """GitHub API を叩く。レート制限/一時障害は指数バックオフでリトライする。

    - 2xx はそのまま返す。
    - 403/429 でレート制限ヘッダがある場合は ``X-RateLimit-Reset`` まで待つ。
    - 5xx は指数バックオフでリトライする。
    - リトライ上限を超えた場合や 4xx（レート以外）は ``raise_for_status`` で送出する。
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = httpx.request(method, url, headers=headers, params=params, timeout=30.0)
        except httpx.HTTPError as exc:  # 接続エラー等は一時障害とみなしてリトライ
            last_exc = exc
            time.sleep(_BACKOFF_BASE**attempt)
            continue

        if resp.is_success:
            return resp

        if _is_rate_limited(resp):
            _sleep_until_rate_reset(resp, attempt)
            continue
        if resp.status_code >= 500:
            time.sleep(_BACKOFF_BASE**attempt)
            continue

        resp.raise_for_status()

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"GitHub API へのリクエストが {_MAX_RETRIES} 回失敗しました: {method} {url}")


def _is_rate_limited(resp: httpx.Response) -> bool:
    if resp.status_code == 429:
        return True
    return resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0"


def _sleep_until_rate_reset(resp: httpx.Response, attempt: int) -> None:
    reset = resp.headers.get("X-RateLimit-Reset")
    if reset is not None:
        wait = max(0.0, float(reset) - time.time()) + 1.0
        # レート枠の復活は最大でも数十分。暴走防止に上限を設ける。
        time.sleep(min(wait, 60.0))
    else:
        time.sleep(_BACKOFF_BASE**attempt)


def _token_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"token {token}",
        "Accept": _ACCEPT,
        "X-GitHub-Api-Version": _API_VERSION,
    }


# ── リポジトリ列挙 ──────────────────────────────────────


def list_installation_repositories(token: str) -> list[tuple[str, str, str]]:
    """インストールがアクセス可能な全リポジトリを ``(owner, repo, default_branch)`` で返す。

    新規リポジトリの追加・削除はこの列挙で毎回自動追従する。
    """
    repos: list[tuple[str, str, str]] = []
    page = 1
    while True:
        resp = _request(
            "GET",
            f"{GITHUB_API}/installation/repositories",
            headers=_token_headers(token),
            params={"per_page": 100, "page": page},
        )
        data: dict[str, Any] = resp.json()
        items: list[dict[str, Any]] = data.get("repositories", [])
        for item in items:
            owner = item["owner"]["login"]
            repos.append((owner, item["name"], item.get("default_branch", "main")))
        if len(items) < 100:
            break
        page += 1
    return repos


def get_default_branch(token: str, owner: str, repo: str) -> str:
    resp = _request("GET", f"{GITHUB_API}/repos/{owner}/{repo}", headers=_token_headers(token))
    data: dict[str, Any] = resp.json()
    branch: str = data.get("default_branch", "main")
    return branch


# ── Skill 探索（Git Trees → Contents）───────────────────


def _get_repo_tree(token: str, owner: str, repo: str, ref: str) -> list[dict[str, Any]]:
    """Git Trees API（recursive=1）でリポジトリ全体のツリーを取得する。"""
    resp = _request(
        "GET",
        f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{ref}",
        headers=_token_headers(token),
        params={"recursive": "1"},
    )
    data: dict[str, Any] = resp.json()
    tree: list[dict[str, Any]] = data.get("tree", [])
    return tree


def _find_skill_dirs(tree: list[dict[str, Any]]) -> dict[str, str]:
    """ツリーから SKILL.md を見つけ、``{skill_dir: skill_md_path}`` を返す。

    SKILL.md のあるディレクトリを1 Skill 単位とみなす（root 直下なら skill_dir=""）。
    ファイル名のみ大文字小文字を許容する。
    """
    skill_dirs: dict[str, str] = {}
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        path: str = entry["path"]
        if path.rsplit("/", 1)[-1].lower() == _SKILL_FILENAME:
            skill_dir = path.rsplit("/", 1)[0] if "/" in path else ""
            skill_dirs[skill_dir] = path
    return skill_dirs


def _files_under_dir(tree: list[dict[str, Any]], skill_dir: str) -> list[str]:
    """``skill_dir`` 配下（その SKILL.md と同階層以下）の blob パス一覧を返す。"""
    prefix = f"{skill_dir}/" if skill_dir else ""
    paths: list[str] = []
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        path: str = entry["path"]
        if skill_dir == "" or path.startswith(prefix):
            paths.append(path)
    return paths


def get_file_content(token: str, owner: str, repo: str, path: str, ref: str) -> bytes:
    """Contents API でファイル本文（生バイト列）を取得する。"""
    resp = _request(
        "GET",
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
        headers=_token_headers(token),
        params={"ref": ref},
    )
    data: dict[str, Any] = resp.json()
    encoding = data.get("encoding")
    if encoding == "base64":
        return base64.b64decode(data["content"])
    # encoding が "none"（巨大ファイル等）の場合は download_url から取得する。
    download_url = data.get("download_url")
    if download_url:
        raw = _request("GET", download_url, headers=_token_headers(token))
        return raw.content
    raise RuntimeError(f"ファイル本文を取得できませんでした: {owner}/{repo}/{path}")


# ── コミットメタ（作者・更新日）─────────────────────────


def get_latest_commit_meta(
    token: str, owner: str, repo: str, path: str, ref: str
) -> tuple[str | None, datetime | None]:
    """Commits API で ``path`` を最後に変更したコミットの作者と日時を取得する。"""
    resp = _request(
        "GET",
        f"{GITHUB_API}/repos/{owner}/{repo}/commits",
        headers=_token_headers(token),
        params={"path": path or ".", "sha": ref, "per_page": 1},
    )
    data: list[dict[str, Any]] = resp.json()
    if not data:
        return None, None
    commit = data[0]
    author = _extract_author(commit)
    last_commit_at = _extract_commit_date(commit)
    return author, last_commit_at


def _extract_author(commit: dict[str, Any]) -> str | None:
    # GitHub アカウントに紐づく場合は login、無ければ commit 上の author 名にフォールバック。
    gh_author = commit.get("author")
    if isinstance(gh_author, dict) and gh_author.get("login"):
        login: str = gh_author["login"]
        return login
    commit_author = commit.get("commit", {}).get("author", {})
    name = commit_author.get("name")
    return name if isinstance(name, str) else None


def _extract_commit_date(commit: dict[str, Any]) -> datetime | None:
    date_str = commit.get("commit", {}).get("author", {}).get("date")
    if not isinstance(date_str, str):
        return None
    # GitHub は "2026-06-25T01:23:45Z" 形式の ISO8601 を返す。
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))


# ── content_hash ────────────────────────────────────────


def compute_content_hash(files: list[SkillFile]) -> str:
    """SKILL.md ＋関連ファイル群をまとめた SHA-256 を計算する。

    パスでソートして順序を安定化し、``path\\0content`` を連結してハッシュする。
    差分検知（前回と同一なら LLM 処理をスキップ）の鍵になる。
    """
    digest = hashlib.sha256()
    for f in sorted(files, key=lambda x: x.path):
        digest.update(f.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(f.content)
        digest.update(b"\0")
    return digest.hexdigest()


# ── 収集（高レベル API）─────────────────────────────────


def collect_skills_from_repo(
    token: str, owner: str, repo: str, default_branch: str | None = None
) -> list[CollectedSkill]:
    """1リポジトリから全 Skill を収集する。"""
    ref = default_branch or get_default_branch(token, owner, repo)
    tree = _get_repo_tree(token, owner, repo, ref)
    skill_dirs = _find_skill_dirs(tree)

    collected: list[CollectedSkill] = []
    for skill_dir, skill_md_path in skill_dirs.items():
        file_paths = _files_under_dir(tree, skill_dir)
        files = [SkillFile(path=p, content=get_file_content(token, owner, repo, p, ref)) for p in file_paths]
        skill_md = next(f for f in files if f.path == skill_md_path)
        related = [f for f in files if f.path != skill_md_path]
        author, last_commit_at = get_latest_commit_meta(token, owner, repo, skill_dir, ref)
        collected.append(
            CollectedSkill(
                owner=owner,
                repo=repo,
                skill_dir=skill_dir,
                skill_md_path=skill_md_path,
                skill_md=skill_md,
                related_files=related,
                author=author,
                last_commit_at=last_commit_at,
                content_hash=compute_content_hash(files),
            )
        )
    return collected


def collect_skills(target: str) -> list[CollectedSkill]:
    """``owner/repo``（単一）または ``owner``（Org/インストール全体）から Skill を収集する。

    認証は App秘密鍵 → JWT → Installation Token の順で自動的に行う。
    """
    app_jwt = generate_app_jwt()

    if "/" in target:
        owner, repo = target.split("/", 1)
        installation_id = get_installation_id_for_repo(app_jwt, owner, repo)
        token = get_installation_token(app_jwt, installation_id)
        return collect_skills_from_repo(token, owner, repo)

    # owner のみ: Org のインストール配下を全列挙して巡回する。
    org = target
    installation_id = get_installation_id_for_org(app_jwt, org)
    token = get_installation_token(app_jwt, installation_id)

    all_skills: list[CollectedSkill] = []
    for owner, repo, default_branch in list_installation_repositories(token):
        try:
            all_skills.extend(collect_skills_from_repo(token, owner, repo, default_branch))
        except httpx.HTTPError as exc:  # 1リポジトリの失敗で全体を止めない（spec: status='error'）
            print(f"[WARN] {owner}/{repo} の収集に失敗: {exc}", file=sys.stderr)
    return all_skills


# ── CLI（動作確認用）────────────────────────────────────


def _print_skills(skills: list[CollectedSkill]) -> None:
    if not skills:
        print("SKILL.md は見つかりませんでした。")
        return
    print(f"=== {len(skills)} 件の Skill を収集しました ===\n")
    for s in skills:
        print(f"# {s.owner}/{s.repo} :: {s.skill_md_path}")
        print(f"  author        : {s.author}")
        print(f"  last_commit_at: {s.last_commit_at}")
        print(f"  content_hash  : {s.content_hash}")
        related = ", ".join(f.path for f in s.related_files) or "(なし)"
        print(f"  related_files : {related}")
        print("  --- SKILL.md ---")
        print("\n".join(f"  | {line}" for line in s.skill_md.text.splitlines()))
        print()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m skillshub.shared.tools.github_tools <owner/repo | owner>", file=sys.stderr)
        return 2
    skills = collect_skills(args[0])
    _print_skills(skills)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
