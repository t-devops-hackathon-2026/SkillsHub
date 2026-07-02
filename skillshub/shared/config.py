from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def get_secret(name: str) -> str:
    """環境変数、なければ Secret Manager から値を取得する。

    仕様: 環境変数が未設定（None）の場合だけでなく、空文字 "" の場合も
    「未設定」とみなして Secret Manager にフォールバックする。本関数が扱うのは
    接続文字列や鍵などのシークレットで、空文字が有効な値になることはないため。
    空文字を有効値として扱いたい用途が将来生じた場合はこの分岐を見直すこと。
    """
    value = os.environ.get(name)
    if value:  # None と "" の両方を未設定扱いにする（上記 docstring 参照）
        return value
    return _get_from_secret_manager(name)


def _get_from_secret_manager(name: str) -> str:
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise ValueError(
            f"環境変数 '{name}' が未設定で、GOOGLE_CLOUD_PROJECT も未設定のため "
            "Secret Manager にフォールバックできません"
        )

    client = _get_sm_client()
    secret_path = f"projects/{project_id}/secrets/{name}/versions/latest"
    response = client.access_secret_version(request={"name": secret_path})
    return str(response.payload.data.decode("utf-8"))


@lru_cache(maxsize=1)
def _get_sm_client():  # type: ignore[no-untyped-def]
    from google.cloud import secretmanager

    return secretmanager.SecretManagerServiceClient()


def get_database_url() -> str:
    return get_secret("DATABASE_URL")


# 重複検出の類似度しきい値。過検出回避のため高め（0.88）を既定にし、環境変数で調整可能にする。
_DEFAULT_DEDUP_THRESHOLD = 0.88

# 鮮度判定: 最終コミットからの経過日数がこれ以下なら current。90 は暫定値で、実データで調整する前提。
_DEFAULT_STALE_DAYS = 90


def get_dedup_threshold() -> float:
    """重複検出の cosine 類似度しきい値を取得する（既定 0.88、env ``DEDUP_THRESHOLD``）。

    env 未設定・空文字の場合は既定値を使う（get_secret と同じく空文字は未設定扱い）。
    """
    value = os.environ.get("DEDUP_THRESHOLD")
    if not value:
        return _DEFAULT_DEDUP_THRESHOLD
    return float(value)


def get_stale_days() -> int:
    """鮮度 stale 判定の経過日数しきい値を取得する（既定 90、env ``UPDATE_STALE_DAYS``）。

    env 未設定・空文字の場合は既定値を使う（get_secret と同じく空文字は未設定扱い）。
    テストが env を都度差し替えるため、キャッシュせず呼び出しごとに読む。
    """
    value = os.environ.get("UPDATE_STALE_DAYS")
    if not value:
        return _DEFAULT_STALE_DAYS
    return int(value)
