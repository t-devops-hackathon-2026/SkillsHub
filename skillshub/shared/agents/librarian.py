"""司書オーケストレーション: Collector → Analyzer → 鮮度判定 → 永続化 → 埋め込み → 重複検出。

#9 で Collector（ADK BaseAgent・output_key で受け渡し）→ Analyzer（ADK LlmAgent・構造化）→
鮮度判定（コード）→ needs_update なら update 下書き生成、までを DB 非依存で組んだ
（``collect_and_analyze``）。#16 でこれを 1 リポジトリ分のバッチに拡張し、永続化と
埋め込み生成・重複検出（``run_deduper_for_skill``）まで一気通貫で行う統括関数
``run_librarian_for_repo`` を追加した。

役割分担: Collector / Analyzer は ADK で動かすが、Embed / Dedup は決定論的処理なので
（#7 結論に従い）ADK エージェント化せず関数を直接呼ぶ。DB 書き込みの実体は
``shared.services`` / ``shared.tools.ai_tools`` に委譲し、本モジュールは流れの統括に徹する。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from skillshub.shared.agents.analyzer import analyze_skill, draft_update
from skillshub.shared.agents.collector import DEFAULT_OUTPUT_KEY, CollectorAgent
from skillshub.shared.schemas import AnalyzedSkill, RawSkill, UpdateStatus
from skillshub.shared.update_status import compute_update_status

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from skillshub.shared.tools.ai_tools import EmbeddingFn

_APP_NAME = "skillshub-librarian"
_USER_ID = "librarian"


@dataclass
class AnalyzedResult:
    """1 Skill の解析〜鮮度判定の結果。永続化は呼び出し側（services）が行う。"""

    raw: RawSkill
    analyzed: AnalyzedSkill
    update_status: UpdateStatus
    update_draft: str | None = None  # needs_update のときのみ diff 下書きを持つ


@dataclass
class LibrarianStats:
    """1 リポジトリ分の収集パイプラインの件数内訳（構造化ログ・テスト検証用）。"""

    collected: int = 0  # 永続化・重複検出に成功した Skill 数（変更分のうち）
    skipped: int = 0  # content_hash 一致でスキップした数
    needs_update: int = 0  # update 提案を新規保存した Skill 数
    merge_suggestions: int = 0  # 新規生成した merge 提案数
    failed: int = 0  # Skill 単位で永続化/重複検出に失敗した数


@dataclass
class LibrarianRunResult:
    """``run_librarian_for_repo`` の結果。件数内訳（stats）と解析結果（results）を持つ。"""

    stats: LibrarianStats
    results: list[AnalyzedResult] = field(default_factory=list)


async def run_collector(collector: CollectorAgent) -> tuple[list[RawSkill], dict[str, int]]:
    """Collector を ADK Runner で実行し、変更分の ``RawSkill`` と収集内訳メタを取り出す。

    内訳メタ（collected / changed / skipped）は Collector が yield する Event の
    ``custom_metadata`` から拾う。
    """
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    session_id = str(uuid.uuid4())
    await session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
    runner = Runner(app_name=_APP_NAME, agent=collector, session_service=session_service)

    # Collector は LLM を使わないが、Runner の起動にはトリガーメッセージが要る。
    trigger = types.Content(role="user", parts=[types.Part.from_text(text="collect")])
    meta: dict[str, int] = {}
    async for event in runner.run_async(user_id=_USER_ID, session_id=session_id, new_message=trigger):
        if event.custom_metadata:
            meta = {k: int(v) for k, v in event.custom_metadata.items()}

    session = await session_service.get_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
    payload = (session.state.get(collector.output_key) if session else None) or []
    return [RawSkill.model_validate(item) for item in payload], meta


async def collect_and_analyze(
    load_raw_skills: Callable[[], list[RawSkill]],
    load_existing_hashes: Callable[[], dict[str, str]],
) -> tuple[list[AnalyzedResult], dict[str, int]]:
    """収集（変更分のみ）→ 解析 → 鮮度判定 →（必要なら）update 下書き、を順に行う。

    解析結果のリストと、収集内訳メタ（collected / changed / skipped）を返す。
    1 Skill の失敗が全体を止めないよう、各 Skill は独立に処理する。
    """
    collector = CollectorAgent(
        name="collector",
        load_raw_skills=load_raw_skills,
        load_existing_hashes=load_existing_hashes,
        output_key=DEFAULT_OUTPUT_KEY,
    )
    changed, meta = await run_collector(collector)

    results: list[AnalyzedResult] = []
    for raw in changed:
        # 1 Skill の失敗（LLM 認証エラー・構造化失敗など）が全体を止めないよう独立に処理する。
        try:
            analyzed = await analyze_skill(raw)
            status = compute_update_status(raw.last_commit_at, is_possibly_outdated=analyzed.is_possibly_outdated)
            draft = None
            if status is UpdateStatus.NEEDS_UPDATE:
                draft = await draft_update(raw, analyzed.outdated_reason)
            results.append(AnalyzedResult(raw=raw, analyzed=analyzed, update_status=status, update_draft=draft))
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で他Skillの解析・永続化を巻き込まない
            print(f"[WARN] 解析に失敗したためスキップ: {raw.source_path}: {exc}", file=sys.stderr)

    return results, meta


def persist_and_dedup(
    session: Session,
    repo_id: UUID,
    results: list[AnalyzedResult],
    collect_meta: dict[str, int],
    *,
    embed_fn: EmbeddingFn | None = None,
) -> LibrarianRunResult:
    """解析結果を永続化し、各 Skill に埋め込み生成＋重複検出を行う（与えられた session 上で）。

    コミットは呼び出し側（``run_librarian_for_repo`` の get_session）が行う。各 Skill は
    savepoint（``begin_nested``）で囲み、1 Skill の失敗をその Skill 分だけロールバックして
    他 Skill を巻き込まないようにする（仕様: 1件失敗が全体を止めない）。
    """
    from skillshub.shared import services
    from skillshub.shared.agents.deduper import run_deduper_for_skill

    stats = LibrarianStats(skipped=collect_meta.get("skipped", 0))

    for r in results:
        try:
            with session.begin_nested():  # Skill 単位の savepoint（失敗時はこの Skill 分だけ巻き戻す）
                skill, saved_update = services._persist_analyzed_skill(
                    session, repo_id, r.raw, r.analyzed, r.update_status, r.update_draft
                )
                created = run_deduper_for_skill(session, skill, embed_fn=embed_fn)
            stats.collected += 1
            if saved_update:
                stats.needs_update += 1
            stats.merge_suggestions += len(created)
        except Exception as exc:  # noqa: BLE001 - 1 Skill の失敗で他 Skill を巻き込まない
            stats.failed += 1
            print(f"[WARN] 永続化/重複検出に失敗したためスキップ: {r.raw.source_path}: {exc}", file=sys.stderr)

    return LibrarianRunResult(stats=stats, results=results)


def run_librarian_for_repo(
    repo_id: UUID,
    load_raw_skills: Callable[[], list[RawSkill]],
    load_existing_hashes: Callable[[], dict[str, str]],
    *,
    embed_fn: EmbeddingFn | None = None,
) -> LibrarianRunResult:
    """1 リポジトリ分の収集パイプライン全体（収集→解析→鮮度→永続化→埋め込み→重複検出）を実行する。

    収集源（``load_raw_skills``）と既存 hash（``load_existing_hashes``）を差し替えることで、
    ローカル samples でも GitHub（#41）でも同じ統括フローを使える。永続化と埋め込み・重複検出は
    1 つの session（＝1トランザクション）にまとめ、Skill だけ保存されて埋め込み/提案が欠ける
    不整合を防ぐ。``embed_fn`` 未指定なら Vertex AI（テストは決定論フェイクを注入）。
    """
    from skillshub.shared.db import get_session

    results, meta = asyncio.run(collect_and_analyze(load_raw_skills, load_existing_hashes))

    session_scope = contextmanager(get_session)
    with session_scope() as session:
        return persist_and_dedup(session, repo_id, results, meta, embed_fn=embed_fn)
