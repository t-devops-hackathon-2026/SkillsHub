"""司書オーケストレーション（#9 範囲: Collector → Analyzer → 鮮度判定）。

埋め込み・Deduper を含む完全版 SequentialAgent と Cloud Run Jobs エントリは #11 のスコープ。
ここでは Collector（ADK BaseAgent・output_key で受け渡し）→ Analyzer（ADK LlmAgent・構造化）→
鮮度判定（コード）→ needs_update なら update 下書き生成、までを DB 非依存で行う。

DB への永続化は ``shared.services`` 側の責務とし、本モジュールは収集源・既存 hash を
コールバックで受け取り、解析結果（``AnalyzedResult``）のリストを返すだけにする。
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from skillshub.shared.agents.analyzer import analyze_skill, draft_update
from skillshub.shared.agents.collector import DEFAULT_OUTPUT_KEY, CollectorAgent
from skillshub.shared.update_status import compute_update_status
from skillshub.shared.schemas import AnalyzedSkill, RawSkill, UpdateStatus

_APP_NAME = "skillshub-librarian"
_USER_ID = "librarian"


@dataclass
class AnalyzedResult:
    """1 Skill の解析〜鮮度判定の結果。永続化は呼び出し側（services）が行う。"""

    raw: RawSkill
    analyzed: AnalyzedSkill
    update_status: UpdateStatus
    update_draft: str | None = None  # needs_update のときのみ diff 下書きを持つ


async def run_collector(collector: CollectorAgent) -> list[RawSkill]:
    """Collector を ADK Runner で実行し、output_key に書かれた変更分を取り出す。"""
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    session_id = str(uuid.uuid4())
    await session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
    runner = Runner(app_name=_APP_NAME, agent=collector, session_service=session_service)

    # Collector は LLM を使わないが、Runner の起動にはトリガーメッセージが要る。
    trigger = types.Content(role="user", parts=[types.Part.from_text(text="collect")])
    async for _ in runner.run_async(user_id=_USER_ID, session_id=session_id, new_message=trigger):
        pass

    session = await session_service.get_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
    payload = (session.state.get(collector.output_key) if session else None) or []
    return [RawSkill.model_validate(item) for item in payload]


async def collect_and_analyze(
    load_raw_skills: Callable[[], list[RawSkill]],
    load_existing_hashes: Callable[[], dict[str, str]],
) -> list[AnalyzedResult]:
    """収集（変更分のみ）→ 解析 → 鮮度判定 →（必要なら）update 下書き、を順に行う。

    1 Skill の失敗が全体を止めないよう、各 Skill は独立に処理する。
    """
    collector = CollectorAgent(
        name="collector",
        load_raw_skills=load_raw_skills,
        load_existing_hashes=load_existing_hashes,
        output_key=DEFAULT_OUTPUT_KEY,
    )
    changed = await run_collector(collector)

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

    return results
