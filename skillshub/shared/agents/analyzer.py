"""AnalyzerAgent: 司書パイプライン 2 段目（構造化解析・鮮度兆候検知）。

役割分担の方針に従い、Analyzer は「構造化出力組」。``output_schema``（``AnalyzedSkill``）を
持つため tools は持たない（ADK では併用が experimental なので、あえて分担を維持）。モデルは
Gemini Flash 既定。SKILL.md 本文をユーザーメッセージとして渡し、name/description/tags/usage と
古さ兆候（``is_possibly_outdated``）を構造化して返させる。

``draft_update`` は鮮度 ``needs_update`` 検知時のみ呼ぶ別建ての軽量生成（schema なし）。Analyzer
本体を単一責務に保つため、diff 下書き生成は分離している。
"""

from __future__ import annotations

import uuid

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from skillshub.shared.schemas import AnalyzedSkill, RawSkill

MODEL = "gemini-2.5-flash"

_APP_NAME = "skillshub-librarian"
_USER_ID = "librarian"

_ANALYZER_INSTRUCTION = """\
あなたは社内 Skill カタログの解析担当です。入力された SKILL.md 本文を読み、次を抽出してください。

- name: Skill の名前（簡潔に）
- description: 何をする Skill かの説明（1〜2文）
- tags: 検索・分類に使うタグ（日本語可、3〜6個）
- usage: 使い方の要約（1〜2文）
- is_possibly_outdated: 参照している API・ライブラリ・ツールに「deprecated」「廃止」「旧バージョン」
  などの古さの兆候が読み取れる場合のみ true。明確な兆候が無ければ false。
- outdated_reason: is_possibly_outdated が true のとき、どの参照が古いと判断したかを簡潔に。

必ず指定のスキーマに従って出力してください。"""

_UPDATE_DRAFTER_INSTRUCTION = """\
あなたは Skill のメンテナンス担当です。次の SKILL.md は依存（参照API・ツール）が古い可能性が
あります。古い記述を新しくするための「修正方針」を、unified diff 風の下書きとして提案してください。
実コミットは作者が手元で行う前提なので、変更すべき箇所と方向性が伝われば十分です。"""


def build_analyzer_agent() -> LlmAgent:
    """構造化解析を行う LlmAgent を組み立てる（output_schema 付き・tools なし）。"""
    return LlmAgent(
        name="analyzer",
        model=MODEL,
        instruction=_ANALYZER_INSTRUCTION,
        output_schema=AnalyzedSkill,
        output_key="analyzed_skill",
    )


def build_update_drafter_agent() -> LlmAgent:
    """update 提案（diff 下書き）を生成する軽量 LlmAgent（schema なし）。"""
    return LlmAgent(
        name="update_drafter",
        model=MODEL,
        instruction=_UPDATE_DRAFTER_INSTRUCTION,
    )


async def analyze_skill(raw: RawSkill) -> AnalyzedSkill:
    """1 つの ``RawSkill`` を解析し、構造化結果を返す（要 Gemini 認証）。"""
    state = await _run_agent_once(build_analyzer_agent(), raw.skill_md_text)
    analyzed = state.get("analyzed_skill")
    if analyzed is None:
        raise RuntimeError(f"Analyzer が構造化結果を返しませんでした: {raw.source_path}")
    return AnalyzedSkill.model_validate(analyzed)


async def draft_update(raw: RawSkill, outdated_reason: str | None) -> str:
    """needs_update の Skill に対する diff 下書きを生成する（要 Gemini 認証）。"""
    prompt = f"# 古さの根拠\n{outdated_reason or '(不明)'}\n\n# SKILL.md\n{raw.skill_md_text}"
    agent = build_update_drafter_agent()
    return await _run_agent_text_response(agent, prompt)


# ── ADK Runner ヘルパ ───────────────────────────────────


async def _new_runner(agent: LlmAgent) -> tuple[Runner, str]:
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    session_id = str(uuid.uuid4())
    await session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
    runner = Runner(app_name=_APP_NAME, agent=agent, session_service=session_service)
    return runner, session_id


def _user_message(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part.from_text(text=text)])


async def _run_agent_once(agent: LlmAgent, text: str) -> dict[str, object]:
    """エージェントを 1 回実行し、最終 session state を返す（output_key 取得用）。"""
    runner, session_id = await _new_runner(agent)
    async for _ in runner.run_async(user_id=_USER_ID, session_id=session_id, new_message=_user_message(text)):
        pass
    session = await runner.session_service.get_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)
    return dict(session.state) if session else {}


async def _run_agent_text_response(agent: LlmAgent, text: str) -> str:
    """エージェントを 1 回実行し、最終応答テキストを返す（schema なし生成用）。"""
    runner, session_id = await _new_runner(agent)
    response = ""
    async for event in runner.run_async(user_id=_USER_ID, session_id=session_id, new_message=_user_message(text)):
        if event.content and event.content.parts:
            text_parts = [p.text for p in event.content.parts if p.text]
            if text_parts:
                response = "".join(text_parts)
    return response
