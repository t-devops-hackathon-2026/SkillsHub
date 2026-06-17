from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def get_secret(name: str) -> str:
    value = os.environ.get(name)
    if value is not None:
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
    return response.payload.data.decode("utf-8")


@lru_cache(maxsize=1)
def _get_sm_client():  # type: ignore[no-untyped-def]
    from google.cloud import secretmanager

    return secretmanager.SecretManagerServiceClient()


def get_database_url() -> str:
    return get_secret("DATABASE_URL")
