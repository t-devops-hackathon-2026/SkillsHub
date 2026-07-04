"""ビュー間で共有する小さな UI 部品（バッジ・チップ・画面遷移）。

dashboard / search の両方が使う表示ロジックをここに集約し、色や文言の定義が
ビューごとに食い違わないようにする。
"""

from __future__ import annotations

import html

import streamlit as st

from skillshub.shared.schemas import SuggestionType, UpdateStatus

# 更新状態 → (表示ラベル, 背景色, 文字色)。GitHub Primer 風の配色。
UPDATE_STATUS_CONFIG: dict[UpdateStatus, tuple[str, str, str]] = {
    UpdateStatus.CURRENT: ("最新", "#dafbe1", "#1a7f37"),
    UpdateStatus.STALE: ("長期未更新", "#fff8c5", "#9a6700"),
    UpdateStatus.NEEDS_UPDATE: ("要更新", "#ffebe9", "#cf222e"),
}

# 提案タイプ → (表示ラベル, 背景色, 文字色)。実装語（merge 等）は UI に出さない。
SUGGESTION_TYPE_CONFIG: dict[SuggestionType, tuple[str, str, str]] = {
    SuggestionType.MERGE: ("重複の統合", "#ddf4ff", "#0969da"),
    SuggestionType.COMPOSE: ("ワークフロー合成", "#fbefff", "#8250df"),
    SuggestionType.UPDATE: ("内容の更新", "#fff8c5", "#9a6700"),
}


def update_status_badge(status: UpdateStatus) -> str:
    label, bg, color = UPDATE_STATUS_CONFIG[status]
    return (
        f'<span style="background:{bg};color:{color};padding:2px 10px;'
        f"border-radius:12px;font-size:12px;font-weight:600;display:inline-block;"
        f'white-space:nowrap">{label}</span>'
    )


def suggestion_type_badge(type_: SuggestionType) -> str:
    label, bg, color = SUGGESTION_TYPE_CONFIG[type_]
    return (
        f'<span style="background:{bg};color:{color};padding:2px 10px;'
        f"border-radius:12px;font-size:12px;font-weight:600;display:inline-block;"
        f'white-space:nowrap">{label}</span>'
    )


def tag_chip(tag: str) -> str:
    safe = html.escape(tag)
    return (
        f'<span style="background:#ddf4ff;color:#0969da;padding:1px 8px;'
        f'border-radius:12px;font-size:12px">{safe}</span>'
    )


# GitHub Octicons（MIT License, https://github.com/primer/octicons）の 16px パスデータ。
# 絵文字の代わりに使う公式アイコン。使う分だけインラインで持ち、外部アセットは取得しない。
_OCTICON_PATHS: dict[str, str] = {
    "repo": (
        "M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75h-2.5a.75.75 0 0 1 "
        "0-1.5h1.75v-2h-8a1 1 0 0 0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2 11.5Zm10.5-1h-8a1 "
        "1 0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 9h8ZM5 12.25a.25.25 0 0 1 .25-.25h3.5a.25.25 0 0 1 "
        ".25.25v3.25a.25.25 0 0 1-.4.2l-1.45-1.087a.249.249 0 0 0-.3 0L5.4 15.7a.25.25 0 0 1-.4-.2Z"
    ),
    "light-bulb": (
        "M8 1.5c-2.363 0-4 1.69-4 3.75 0 .984.424 1.625.984 2.304l.214.253c.223.264.47.556.673.848."
        "284.411.537.896.621 1.49a.75.75 0 0 1-1.484.211c-.04-.282-.163-.547-.37-.847a8.456 8.456 0 0 "
        "0-.542-.68c-.084-.1-.173-.205-.268-.32C3.201 7.75 2.5 6.766 2.5 5.25 2.5 2.31 4.863 0 8 0s5.5 "
        "2.31 5.5 5.25c0 1.516-.701 2.5-1.328 3.259-.095.115-.184.22-.268.319-.207.245-.383.453-.541."
        "681-.208.3-.33.565-.37.847a.751.751 0 0 1-1.485-.212c.084-.593.337-1.078.621-1.489.203-.292."
        "45-.584.673-.848.075-.088.147-.173.213-.253.561-.679.985-1.32.985-2.304 0-2.06-1.637-3.75-4-3.75Z"
        "M5.75 12h4.5a.75.75 0 0 1 0 1.5h-4.5a.75.75 0 0 1 0-1.5Zm.75 2.75a.75.75 0 0 1 .75-.75h1.5a.75."
        "75 0 0 1 0 1.5h-1.5a.75.75 0 0 1-.75-.75Z"
    ),
    "check": (
        "M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.751.751 0 0 1 "
        ".018-1.042.751.751 0 0 1 1.042-.018L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0Z"
    ),
}


def octicon(name: str, size: int = 16, color: str = "currentColor") -> str:
    """GitHub 公式アイコン（Octicons）のインライン SVG を返す（絵文字の代替）。"""
    return (
        f'<svg aria-hidden="true" viewBox="0 0 16 16" width="{size}" height="{size}" '
        f'fill="{color}" style="vertical-align:text-bottom;flex:none;display:inline-block">'
        f'<path d="{_OCTICON_PATHS[name]}"/></svg>'
    )


def navigate_to_detail(skill_id: str) -> None:
    st.session_state.selected_skill_id = skill_id
    st.session_state.current_view = "detail"
    st.rerun()


# GitHub Primer 風のグローバル CSS。配色・角丸・影のトークンは demos/step1/demo.html と揃える。
# Streamlit の DOM は data-testid で指しており、バージョン更新で外れた場合は該当ルールだけ
# 効かなくなる（表示が壊れることはない）。
_GITHUB_STYLE = """
<style>
:root{
  --gh-bg:#f6f8fa; --gh-card:#ffffff;
  --gh-ink:#1f2328; --gh-muted:#59636e;
  --gh-line:#d1d9e0; --gh-line-soft:#eaeef2;
  --gh-accent:#0969da; --gh-accent-soft:#ddf4ff;
  --gh-success:#1f883d; --gh-success-h:#1a7f37;
  --gh-danger:#cf222e; --gh-danger-h:#a40e26;
  --gh-btn:#f6f8fa; --gh-btn-h:#eef1f4; --gh-btn-bd:rgba(31,35,40,.15);
  --gh-ai:#8250df;
  --gh-grad:linear-gradient(135deg,#0969da 0%,#8250df 100%);
  --gh-shadow:0 1px 0 rgba(31,35,40,.04);
  --gh-shadow-2:0 3px 8px rgba(31,35,40,.09);
  --gh-shadow-3:0 8px 20px rgba(31,35,40,.14);
}

/* ── ベース ── */
html, body, .stApp{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Kaku Gothic ProN",
    "Noto Sans JP",Helvetica,Arial,sans-serif;
}
.stApp{background:var(--gh-bg)}
[data-testid="stHeader"]{background:rgba(246,248,250,.88);backdrop-filter:blur(4px)}
/* 上 padding は固定ヘッダー（60px）に先頭要素が隠れない高さを確保する */
.block-container{max-width:1100px;padding:4.4rem 1.5rem 4rem}
#MainMenu, footer{visibility:hidden}
h1,h2,h3{color:var(--gh-ink);font-weight:600;letter-spacing:0}
hr{border-color:var(--gh-line-soft)}
a{color:var(--gh-accent)}

/* ── サイドバー：GitHub SideNav 風 ── */
[data-testid="stSidebar"]{background:var(--gh-card);border-right:1px solid var(--gh-line)}
[data-testid="stSidebar"] .stButton button{
  width:100%;justify-content:flex-start;text-align:left;
  background:transparent;border:0;box-shadow:none;border-radius:6px;
  color:var(--gh-muted);font-weight:500;padding:.42rem .75rem;transition:background .12s ease}
[data-testid="stSidebar"] .stButton button:hover{background:#f3f4f6;color:var(--gh-ink)}
[data-testid="stSidebar"] .stButton button[kind="primary"],
[data-testid="stSidebar"] .stButton button[data-testid="stBaseButton-primary"]{
  background:var(--gh-accent-soft);color:var(--gh-accent);font-weight:600}
.sh-logo{display:flex;align-items:center;gap:9px;font-size:1.08rem;font-weight:600;color:var(--gh-ink)}
.sh-mark{display:grid;place-items:center;width:27px;height:27px;border-radius:6px;
  background:var(--gh-ink);color:#fff;font-size:14px;font-weight:600}

/* サイドバーの折りたたみボタンはホバー時のみ表示が既定なので、常時表示にする */
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarHeader"] button{
  opacity:1 !important;visibility:visible !important}

/* サイドバーヘッダーは折りたたみボタンだけなので右上に浮かせ、
   コンテンツ（ロゴ）を最上部から始めてボタンと同じ高さに揃える */
[data-testid="stSidebarHeader"]{position:absolute;top:0;right:0;z-index:2;padding:16px 14px 0 0}
[data-testid="stSidebarHeader"] [data-testid="stLogoSpacer"]{display:none}
[data-testid="stSidebarUserContent"]{padding-top:24px}

/* ── ボタン：Primer btn / btn-primary ── */
.stButton button, [data-testid="stFormSubmitButton"] button{
  border-radius:6px;font-weight:600;font-size:.85rem;
  border:1px solid var(--gh-btn-bd);background:var(--gh-btn);color:var(--gh-ink);
  box-shadow:var(--gh-shadow);transition:background .12s ease}
.stButton button:hover, [data-testid="stFormSubmitButton"] button:hover{
  background:var(--gh-btn-h);border-color:var(--gh-btn-bd);color:var(--gh-ink)}
[data-testid="stMain"] .stButton button[kind="primary"],
[data-testid="stMain"] [data-testid="stFormSubmitButton"] button[kind="primary"],
section.main .stButton button[kind="primary"],
section.main [data-testid="stFormSubmitButton"] button[kind="primary"]{
  background:var(--gh-success);border-color:rgba(31,35,40,.15);color:#fff;
  box-shadow:0 1px 0 rgba(31,35,40,.1)}
[data-testid="stMain"] .stButton button[kind="primary"]:hover,
[data-testid="stMain"] [data-testid="stFormSubmitButton"] button[kind="primary"]:hover,
section.main .stButton button[kind="primary"]:hover,
section.main [data-testid="stFormSubmitButton"] button[kind="primary"]:hover{
  background:var(--gh-success-h);color:#fff}

/* 無効化ボタンのアイコンをスピナーとして回転させる（「同期中...」表示用）。
   現状 disabled ボタンはこの用途のみなので、通常の disabled 全般に対して安全に適用できる。 */
@keyframes sh-spin{to{transform:rotate(360deg)}}
.stButton button:disabled [data-testid="stIconMaterial"]{
  display:inline-block;animation:sh-spin 1s linear infinite}

/* ── border 付きコンテナ：Primer Box（カード）──
   st.container(border=True, key=...) の key（skill_box_ / _box / repo_box_）で狙う。 */
.stVerticalBlock[class*="st-key-skill_box_"],
.stVerticalBlock[class*="st-key-hist_"],
.stVerticalBlock[class*="st-key-repo_box_"],
.stVerticalBlock[class*="st-key-suggestion_box_"]{
  background:var(--gh-card);border:1px solid var(--gh-line);border-radius:8px;
  padding:1rem 1rem 1.25rem;box-shadow:var(--gh-shadow),var(--gh-shadow-2);
  transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease}
.stVerticalBlock[class*="st-key-skill_box_"]:hover,
.stVerticalBlock[class*="st-key-hist_"]:hover{
  border-color:#afb8c1;box-shadow:var(--gh-shadow-3);transform:translateY(-2px)}
[data-testid="stForm"]{background:var(--gh-card);border:1px solid var(--gh-line);
  border-radius:8px;box-shadow:var(--gh-shadow),var(--gh-shadow-2)}

/* カード内は要素間隔を詰めて「名前・説明・タグ」をひとかたまりに見せる。
   説明は常に2行分の高さを取り、カードの高さを揃える。 */
.stVerticalBlock[class*="st-key-skill_box_"]{gap:.45rem}

/* 状態バッジはカード右上に絶対配置し、スキル名と同じ高さに固定する。
   バッジを包む要素コンテナごとフローから外す（gap の幽霊余白を作らない）。 */
.stVerticalBlock[class*="st-key-skill_box_"],
.stVerticalBlock[class*="st-key-hist_"],
.stVerticalBlock[class*="st-key-suggestion_box_"]{position:relative}
[data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] .sh-badge-abs){
  position:absolute;top:13px;right:1rem;width:auto;margin:0;z-index:1}
div[class*="st-key-"][class*="_card_"]{padding-right:104px}
.stVerticalBlock[class*="st-key-skill_box_"] [data-testid="stCaptionContainer"] p{
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
  min-height:2.9em;font-size:.85rem;color:#454c54;line-height:1.7}

/* 中央寄せの矯正：タイトルリンク・サイドバー・メトリクスは左揃え
   （ボタン内側の flex ラッパーが中央寄せのため、内側まで指定する） */
div[class*="st-key-"][class*="_card_"] button p,
div[class*="st-key-"][class*="_link_"] button p{text-align:left;width:100%;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
div[class*="st-key-"][class*="_card_"] button>div,
div[class*="st-key-"][class*="_link_"] button>div{justify-content:flex-start;width:100%;min-width:0}
[data-testid="stSidebar"] .stButton button p{text-align:left;width:100%}
[data-testid="stSidebar"] .stButton button>div{justify-content:flex-start;width:100%}
[data-testid="stMetric"]{text-align:left}

/* ── Skill カードのタイトル（key に "_card_" を含むボタン）：リンク風 ──
   "_link_" はカード内リンク用の小さめ変種（右上バッジ回避の padding を持たない）。 */
div[class*="st-key-"][class*="_card_"] button,
div[class*="st-key-"][class*="_link_"] button{
  background:transparent;border:0;box-shadow:none;padding:0;
  min-height:0;height:auto;
  color:var(--gh-accent);font-weight:600;font-size:1.05rem;
  justify-content:flex-start;text-align:left}
div[class*="st-key-"][class*="_card_"] button:hover,
div[class*="st-key-"][class*="_link_"] button:hover{
  background:transparent;color:var(--gh-accent);text-decoration:underline}
div[class*="st-key-"][class*="_link_"] button{font-size:.95rem}

/* ── サマリー（st.metric）── */
[data-testid="stMetric"]{background:var(--gh-card);border:1px solid var(--gh-line);
  border-radius:8px;padding:14px 16px;box-shadow:var(--gh-shadow),var(--gh-shadow-2)}
[data-testid="stMetricLabel"]{color:var(--gh-muted)}
[data-testid="stMetricValue"]{font-weight:600}

/* ── 入力・セレクト：Primer form control ── */
.stTextInput input{background:var(--gh-card);border:1px solid var(--gh-line);
  border-radius:6px;color:var(--gh-ink)}
.stTextInput input:focus{border-color:var(--gh-accent);box-shadow:0 0 0 3px rgba(9,105,218,.15)}
[data-baseweb="select"]>div{background:var(--gh-card);border-color:var(--gh-line);border-radius:6px}
[data-baseweb="select"]:hover>div{border-color:#afb8c1}
[data-testid="stChatInput"]{background:var(--gh-card);border:1px solid var(--gh-line);border-radius:6px}

/* マルチセレクトの選択タグ：既定の赤ではなく Primer のトピックタグ風に */
.stMultiSelect [data-baseweb="tag"]{
  background:var(--gh-accent-soft);color:var(--gh-accent);border-radius:2em}
.stMultiSelect [data-baseweb="tag"] span{color:var(--gh-accent)}
.stMultiSelect [data-baseweb="tag"] svg{fill:var(--gh-accent)}

/* ── チャット：GitHub の issue コメント風 ── */
[data-testid="stChatMessage"]{background:var(--gh-card);border:1px solid var(--gh-line);
  border-radius:6px;box-shadow:var(--gh-shadow);padding:.85rem 1rem}
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]){
  background:var(--gh-accent-soft);border-color:#b6e3ff}

/* チャット入力（画面下固定）はメインコンテンツと幅・背景を揃える
   （既定ではコンテナ幅が block-container と一致せず、検索バーだけ間延びして見える） */
[data-testid="stBottom"]{background:var(--gh-bg)}
[data-testid="stBottom"]>div{background:transparent}
[data-testid="stBottomBlockContainer"]{
  max-width:1100px;padding-left:1.5rem;padding-right:1.5rem;background:transparent}

/* ── ステータス・アラート ── */
[data-testid="stExpander"]{background:var(--gh-card);border:1px solid var(--gh-line);border-radius:8px}
[data-testid="stAlert"]{border-radius:8px}

/* ── 司書エージェント：Copilot 風のグラデーションで「AI が触れる場所」を示す ──
   グラデーションは検索バー・確信度・チャットのエージェント発言だけに限定する。 */
.sh-ai-eyebrow{display:inline-block;font-size:12px;font-weight:600;letter-spacing:.02em;
  background:var(--gh-grad);-webkit-background-clip:text;background-clip:text;color:transparent}
div[class*="st-key-agent_bar_query"] input{
  border:1px solid transparent;border-radius:8px;
  background:linear-gradient(var(--gh-card),var(--gh-card)) padding-box,var(--gh-grad) border-box;
  box-shadow:var(--gh-shadow-2)}
div[class*="st-key-agent_bar_query"] input:focus{
  border-color:transparent;box-shadow:0 0 0 3px rgba(130,80,223,.18)}
div[class*="st-key-agent_bar_submit"] button{
  background:var(--gh-grad);color:#fff;border:0;font-weight:600;box-shadow:var(--gh-shadow-2)}
div[class*="st-key-agent_bar_submit"] button:hover{
  background:var(--gh-grad);color:#fff;filter:brightness(1.07)}
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]){
  border-left:3px solid var(--gh-ai)}
.stProgress > div > div > div > div{background:var(--gh-grad)}

/* サイドバーの同期パネル */
.sh-last{color:var(--gh-muted);font-size:12px;text-align:center;margin:8px 0 0}
[data-testid="stSidebar"] div[class*="st-key-sync_all"] .stButton button{
  background:var(--gh-success);border:1px solid rgba(31,35,40,.15);color:#fff;
  font-weight:600;box-shadow:var(--gh-shadow-2)}
[data-testid="stSidebar"] div[class*="st-key-sync_all"] .stButton button:hover{
  background:var(--gh-success-h);color:#fff}
[data-testid="stSidebar"] div[class*="st-key-sync_all"] .stButton button p{
  text-align:center;width:100%}
[data-testid="stSidebar"] div[class*="st-key-sync_all"] .stButton button>div{
  justify-content:center}

/* ── 提案カードの対象 Skill：ラベル＋リンクを1行に流す（あふれたら折り返し）── */
.stVerticalBlock[class*="st-key-"][class*="_targets"]{
  flex-direction:row;flex-wrap:wrap;align-items:baseline;gap:.1rem .75rem}
.stVerticalBlock[class*="st-key-"][class*="_targets"]>[data-testid="stElementContainer"]{
  width:auto;flex:0 0 auto}

/* ── 表示切り替え（st.segmented_control）：Primer SegmentedControl 風 ── */
[data-testid="stSegmentedControl"] button{
  font-size:.85rem;font-weight:500;color:var(--gh-muted)}
[data-testid="stSegmentedControl"] button:hover{color:var(--gh-ink)}
[data-testid="stSegmentedControl"] button[data-testid="stBaseButton-segmented_controlActive"]{
  background:var(--gh-accent-soft);color:var(--gh-accent);font-weight:600;border-color:transparent}

/* サイドバーのデモリセット：Primer btn-danger 風。
   トリガー（popover）は outline、popover 内の実行ボタンはベタ塗りにする。
   popover の中身は overlay に描画されサイドバー外のため、実行ボタンは key だけで指す。 */
[data-testid="stSidebar"] div[class*="st-key-demo_reset_area"] [data-testid="stPopover"] button{
  width:100%;justify-content:center;border-radius:6px;
  background:transparent;border:1px solid var(--gh-btn-bd);
  color:var(--gh-danger);font-weight:600;font-size:.85rem}
[data-testid="stSidebar"] div[class*="st-key-demo_reset_area"] [data-testid="stPopover"] button:hover{
  background:var(--gh-danger);border-color:var(--gh-danger);color:#fff}
[data-testid="stSidebar"] div[class*="st-key-demo_reset_area"] [data-testid="stPopover"] button p{
  text-align:center;width:100%}
div[class*="st-key-demo_reset_confirm"] button{
  background:var(--gh-danger);border:1px solid rgba(31,35,40,.15);color:#fff;font-weight:600}
div[class*="st-key-demo_reset_confirm"] button:hover{
  background:var(--gh-danger-h);border-color:var(--gh-danger-h);color:#fff}

/* ── レスポンシブ：640px 以下でカラムを縦積みに ── */
@media (max-width:640px){
  .block-container{padding-left:1rem;padding-right:1rem;padding-top:1.4rem}
  [data-testid="stHorizontalBlock"]{flex-wrap:wrap}
  [data-testid="stHorizontalBlock"]>[data-testid="stColumn"],
  [data-testid="stHorizontalBlock"]>[data-testid="column"]{
    min-width:100% !important;flex:1 1 100% !important}
  [data-testid="stMetric"]{padding:10px 12px}
}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition-duration:.01ms !important;animation-duration:.01ms !important}
}
</style>
"""


def inject_github_style() -> None:
    """GitHub Primer 風のグローバル CSS を注入する（各再描画の先頭で1回呼ぶ）。"""
    st.markdown(_GITHUB_STYLE, unsafe_allow_html=True)
