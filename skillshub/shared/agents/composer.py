"""Composer: 検索候補の Skill を組み合わせた合成ワークフローを構造化提案する。

仕様の正は docs/designs/step1/step1.md「自然言語検索」。重い推論なのでモデルは Pro 系を使う。
Searcher→Composer は機械的に直列化せず、サービス層（shared.services.search_skills）が
候補数を見て 2 件以上のときだけ起動する。

Searcher/Deduper と同じ方針で、実処理はテスト容易な純 Python（``run_composer``）として
実装し、Gemini への構造化出力リクエストは差し替え可能（``generate_fn`` の注入）にする。

LLM が生成するのは title/body のみ。対象 Skill（``target_skill_ids``）は候補から
サービス側で機械的に埋める（UUID を LLM に生成させない）。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import BaseModel

from skillshub.shared.schemas import ComposeSuggestion, SearchResultItem

# 重い推論なので Pro 系（仕様の Flash/Pro 使い分け）。
COMPOSER_MODEL = "gemini-2.5-pro"

# 合成提案は候補が 2 件以上のときだけ生成する（仕様: 候補2件以上で Composer 起動）。
_MIN_CANDIDATES_FOR_COMPOSE = 2


class ComposerWorkflow(BaseModel):
    """Composer（LLM）が出力するワークフロー本文（構造化出力のスキーマ兼 戻り値型）。

    ``needed`` は「複数 Skill を組み合わせる価値があるか」の LLM 自身の判断。
    単一 Skill で足りるクエリに無理やり合成を出さないためのフラグ（False なら提案しない）。
    対象 Skill の id は LLM に出させず、サービス側が候補から付与する（``ComposeSuggestion``）。
    """

    needed: bool = True
    title: str
    body: str


# クエリ・候補・モデル名から合成ワークフローを生成する関数型。
# 既定は Gemini Pro だが、テストでは決定論的なフェイクを注入できる。
ComposeGenerateFn = Callable[[str, list[SearchResultItem], str], "ComposerWorkflow | None"]


def run_composer(
    query: str,
    items: list[SearchResultItem],
    *,
    model: str = COMPOSER_MODEL,
    generate_fn: ComposeGenerateFn | None = None,
) -> ComposeSuggestion | None:
    """候補 Skill を組み合わせた合成提案を返す（決定論的本体）。

    候補が 2 件未満なら合成しない（``None``）。「単一の Skill で足りる」と LLM が判断した
    場合（``needed=False``）や、LLM 呼び出しが失敗した場合も ``None`` を返し、
    検索結果自体（候補リスト）は呼び出し側で必ず返せるようにする（graceful degradation）。

    Returns:
        ``target_skill_ids`` に候補全件を持つ ``ComposeSuggestion``。生成不可・合成不要なら ``None``。
    """
    if len(items) < _MIN_CANDIDATES_FOR_COMPOSE:
        return None

    generate = generate_fn or _generate_compose
    try:
        workflow = generate(query, items, model)
    except Exception:  # noqa: BLE001 — LLM/GCP 失敗時は合成なしで検索結果を返す
        return None

    if workflow is None or not workflow.needed:
        return None

    return ComposeSuggestion(
        title=workflow.title,
        body=workflow.body,
        target_skill_ids=[item.skill.id for item in items],
    )


def _generate_compose(query: str, items: list[SearchResultItem], model: str) -> ComposerWorkflow:
    """Gemini Pro で合成ワークフロー（title/body）を構造化生成する（既定の生成実装）。

    ``vertexai`` は関数内で遅延 import する（GCP 認証が無い環境では呼ばない）。
    """
    from vertexai.generative_models import GenerationConfig, GenerativeModel

    listed = "\n".join(f"- {item.skill.name}: {item.skill.description}" for item in items)
    prompt = (
        "あなたは社内 Skill を組み合わせてワークフローを設計するアシスタントです。\n"
        f"ユーザーのやりたいこと: 「{query}」\n\n"
        "次の Skill 候補があります。\n"
        f"{listed}\n\n"
        "複数の Skill を順に使うことで、単一の Skill では実現できない価値が生まれる場合にだけ、"
        "その手順（ワークフロー）を提案してください。\n"
        "- 単一の Skill で目的を達成できる場合や、組み合わせが不自然な場合は"
        " needed を false、title と body を空文字にしてください。\n"
        "- 提案する場合は needed を true にし、title はワークフローの短い名前、"
        "body はどの Skill をどの順で使い、何が得られるかを説明する文章にしてください。"
    )
    schema = {
        "type": "object",
        "properties": {
            "needed": {"type": "boolean"},
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["needed", "title", "body"],
    }
    response = GenerativeModel(model).generate_content(
        prompt,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.4,
        ),
    )
    data = json.loads(response.text)
    return ComposerWorkflow(needed=bool(data["needed"]), title=str(data["title"]), body=str(data["body"]))
