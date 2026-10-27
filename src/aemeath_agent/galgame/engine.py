"""心动模式 · Galgame 引擎。

每轮对话一次结构化 LLM 调用：模型在保持「爱弥斯」本我的前提下扮演女友，
输出 JSON 契约（回复 / 旁白 / 好感度变化 / 事件）。事件有三个来源：

1. **随机事件**：引擎按概率掷骰，在普通事件清单中加权抽取（时间/天气契合者权重更高）；
2. **模型决策事件**：未掷骰时，模型可根据聊天内容自行开启允许的事件；
3. **心链事件**：好感度达标且度过冷却期后进入候选，叙事分量更重、收益更高，
   每个周目只发生一次。

对话格式约定：``「」`` 内为说出口的台词，``（）`` 内为心理活动与具体动作，
便于前端把动作/心理单独排版。

现实世界（:mod:`.world`）提供的时间、天气、地点会注入 system prompt，
让对话与事件自然呼应此刻的真实环境。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..llm.agent_router import parse_json
from ..llm.client import HttpLLMClient
from ..persona import ACTIVE_PERSONA
from ..settings import Settings, get_settings
from .events import (
    CHAPTERS,
    ENDINGS,
    EVENTS,
    FRIEND_THRESHOLD,
    HEARTLINK_BONUS,
    LOVE_THRESHOLD,
    TIER_HEARTLINK,
    TIER_NORMAL,
    Chapter,
    GameEvent,
    cg_url,
    eligible_events,
    heartlink_ready,
    normal_events,
    weighted_choice,
    world_weight,
)
from .retrieval import HashingRetriever, Retriever, build_retriever
from .state import (
    ActiveEvent,
    ENDING_FADE,
    ENDING_FRIEND,
    ENDING_LOVE,
    MEMORY_KIND_EVENT,
    MEMORY_KIND_PLOT,
    MEMORY_KIND_PROMISE,
    GalgameSave,
    archive_current,
    iter_archives,
    load_save,
    persist,
    wipe,
)
from .world import WorldContext, get_world

# 每轮触发普通随机事件的概率
EVENT_CHANCE = 0.28
# 心链事件单独掷骰的概率（需已达好感门槛且不在冷却期）
HEARTLINK_CHANCE = 0.30
# 单个事件最多持续轮数（超出强制收尾）
MAX_EVENT_TURNS = 3
# 注入给模型的历史条数
HISTORY_LIMIT = 26
# 普通对话好感度每轮浮动上限
CHAT_DELTA_CAP = 5
# 单轮好感度变化的绝对上限（含所有加成）
MAX_DELTA = 15
# LLM 调用硬超时（秒）：宁可给出降级回复，也不要让前端无限等待
LLM_TIMEOUT = 60
# 回复最大 token：过长会让推理型模型明显变慢
REPLY_MAX_TOKENS = 700

# ---------------------------- 长期记忆 ---------------------------- #
# 兜底压缩间隔（轮）：即使没跨章节，聊满这么多轮也把这段剧情压成一条回忆
MEMORY_INTERVAL = 12
# 每轮注入给模型的记忆条数上限（从存档记忆里按相关度挑选）
MEMORY_KEEP = 8
# 单条回忆的字数上限
MEMORY_CHAR_LIMIT = 120
# 存档内回忆总条数上限（防无限膨胀）
MEMORY_MAX_ITEMS = 24
# 触发「她眼中的你」画像总结的累计 token。主控制台默认一百万（工具型长会话），
# 对一个几十轮的小周目来说永远够不着，所以心动模式用自己的尺度。
MEMORY_SUMMARY_TOKENS = 4000

# ---------------------------- 记忆检索 ---------------------------- #
# 每条记忆的显著度（salience）：影响检索排序，也决定哪类记忆更容易被想起
SALIENCE_PLOT = 0.5        # 剧情片段：日常回忆
SALIENCE_EVENT = 0.6       # 事件经历：开启
SALIENCE_EVENT_END = 0.7   # 事件经历：收尾
SALIENCE_PROMISE = 0.9     # 约定承诺
SALIENCE_HEARTLINK = 1.0   # 心链 / 结局：最高权重
# 显著度对检索得分的调制幅度：最不显著也有 0.7 倍，避免老记忆被彻底压死
_SALIENCE_FLOOR = 0.7

# 约定 / 承诺的触发词：命中即认为这一轮双方说定了什么事
_PROMISE_HINTS = ("约定", "说好", "答应", "拉勾", "拉钩", "下次一起",
                  "一起去", "下次带", "别忘", "记得一定")

# 记忆类型的展示标签（注入提示词时让模型分得清「回忆 / 经历 / 约定」）
_KIND_LABEL = {
    MEMORY_KIND_EVENT: "经历",
    MEMORY_KIND_PROMISE: "约定",
}

# ---------------------------- 离线留言 ---------------------------- #
# 她「没有信号」时最多替她记下几句话（超出丢最旧的，避免信箱无限膨胀）
PENDING_NOTE_MAX = 5
# 单条留言的字数上限
PENDING_NOTE_CHAR_LIMIT = 60

# ---------------------------- 主动陪伴 ---------------------------- #
# 「久别问候」：距上次玩家发言超过这么多小时才主动开口
RESUME_GAP_HOURS = 6.0
# 「闲置搭话」：距上次玩家发言至少这么多秒（前端 5 分钟触发，后端再兜一道）
IDLE_MIN_SECONDS = 180
# 两次主动发言之间的最小间隔（分钟）
PROACTIVE_COOLDOWN_MIN = 20
# 每日主动发言次数上限
PROACTIVE_DAILY_CAP = 4

# JSON 漂移时追加的严格提醒（模型偶尔会只写正文不写 JSON）
_JSON_REMINDER = (
    "你上一条回复不是合法 JSON。请严格只输出一个 JSON 对象"
    "（字段：reply / narration / affinity_delta / event），"
    "不要输出 JSON 以外的任何文字、解释或 Markdown 代码块。"
)

# 剧情压缩提示词：把一段对话压成爱弥斯视角的一条短回忆
_MEMORY_SYSTEM = (
    "你是恋爱剧情记录员。请把下面这段「玩家与爱弥斯」的对话，压缩成一条"
    "以爱弥斯第一人称视角写下的短回忆，供她日后回想。要求：\n"
    "1. 只写 1~2 句、不超过 60 字，中文；\n"
    "2. 抓住具体细节（聊了什么、约定过什么、心情如何），不要空泛抒情；\n"
    "3. 不要复述整段对话，不要出现「用户」这类词，用「你」称呼对方；\n"
    "4. 只输出回忆正文，不要任何解释、标题或 JSON。"
)

_AFFINITY_LEVELS = [
    (20, "陌生"),
    (40, "初识"),
    (60, "亲近"),
    (80, "心动"),
    (101, "深爱"),
]


def affinity_level(value: int) -> str:
    for bound, label in _AFFINITY_LEVELS:
        if value < bound:
            return label
    return "深爱"


def growth_cap(affinity: int, base_cap: int) -> int:
    """好感度成长上限的边际递减：越亲近，增长越慢。

    系数从 1.0 线性降到 0.4（好感 0 → 96+），并保底至少 +2，
    避免高好感阶段完全停滞。
    """
    factor = max(0.4, 1.0 - affinity / 160.0)
    return max(2, int(base_cap * factor + 0.5))


# --------------------------------------------------------------------------- #
# 时间助手（主动陪伴的频控全部基于时间差，读不出时间时按「很久没见」处理）
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse_iso(text: str) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _hours_since(text: str, now: datetime) -> float:
    """距某时刻已过的小时数；无记录/损坏时视为很久（允许主动）。"""
    stamp = _parse_iso(text)
    if stamp is None:
        return float("inf")
    return max(0.0, (now - stamp).total_seconds() / 3600.0)


def _minutes_since(text: str, now: datetime) -> float:
    stamp = _parse_iso(text)
    if stamp is None:
        return float("inf")
    return max(0.0, (now - stamp).total_seconds() / 60.0)


def _world_hint(world: Optional[WorldContext]) -> str:
    """把世界上下文压成一句话尾（用于离线兜底的回忆模板）。"""
    if world is None:
        return "此刻的光景"
    bits = [b for b in (world.time_period, world.weather if world.has_weather else "")
            if b]
    return "、".join(bits) if bits else "此刻的光景"


def _clean_recall(text: str) -> str:
    """清洗模型输出的回忆文本：去代码块、折行、引号包裹。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = " ".join(text.split())
    return text.strip("「」\"'“” ")


def _memory_user(save: "GalgameSave", pairs: List[Dict[str, str]],
                 reason: str) -> str:
    """构造剧情压缩的 user 消息。"""
    chapter = CHAPTERS[min(save.chapter, len(CHAPTERS) - 1)]
    lines = [f"{'爱弥斯' if i['role'] == 'aemeath' else '玩家'}：{i['content']}"
             for i in pairs]
    head = (f"当前章节：{chapter.title} {chapter.subtitle}；"
            f"好感度 {save.affinity}/100。")
    tail = ("\n（这段刚好以章节收束为界，请写出带一点小结感的回忆。）"
            if reason == "transition" else "")
    return head + "\n" + "\n".join(lines) + tail


def _memory_entry(kind: str, text: str, *, turn: int, chapter: int,
                  salience: float, tags: Optional[List[str]] = None,
                  entities: Optional[List[str]] = None) -> Dict[str, Any]:
    """构造一条结构化记忆。

    字段说明：``kind`` 区分剧情 / 经历 / 约定；``tags`` 与 ``entities`` 是检索
    与展示用的关键词（例如事件 id、事件标题、约定里出现的名词）；``salience``
    是显著度，越高越容易被想起来。老存档里的裸记忆（只有 text）在
    :func:`_norm_memory` 中被补齐成剧情类。
    """
    return {
        "kind": kind,
        "text": (text or "").strip()[:MEMORY_CHAR_LIMIT],
        "turn": int(turn),
        "chapter": int(chapter),
        "salience": float(salience),
        "tags": [t for t in (tags or []) if t][:6],
        "entities": [e for e in (entities or []) if e][:6],
        "at": _now_iso(),
    }


def _norm_memory(raw: Any) -> Dict[str, Any]:
    """把任意历史形态的记忆条目补成完整结构（供检索与注入使用）。

    早期存档只有 ``{"turn", "chapter", "text", "at"}``，缺 kind/salience/tags。
    这里统一补齐且**不改动存档原文**，保证旧存档读出来照常可玩。
    """
    item = raw if isinstance(raw, dict) else {}
    kind = str(item.get("kind") or MEMORY_KIND_PLOT)
    if kind not in (MEMORY_KIND_PLOT, MEMORY_KIND_EVENT, MEMORY_KIND_PROMISE):
        kind = MEMORY_KIND_PLOT
    try:
        salience = float(item.get("salience") or SALIENCE_PLOT)
    except (TypeError, ValueError):
        salience = SALIENCE_PLOT
    tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    entities = (item.get("entities")
                if isinstance(item.get("entities"), list) else [])
    return {
        "kind": kind,
        "text": str(item.get("text") or "").strip(),
        "salience": max(0.0, min(1.0, salience)),
        "tags": [str(t) for t in tags if str(t).strip()],
        "entities": [str(e) for e in entities if str(e).strip()],
    }


def _memory_mid(text: str) -> str:
    """记忆指纹：正文的短哈希。

    收藏状态存指纹而非下标——记忆会被裁剪、下标会漂移，指纹不会。
    老存档里的记忆没有 id 字段，也不需要：指纹是从正文现算的。
    """
    return hashlib.md5((text or "").strip().encode("utf-8")).hexdigest()[:10]


def _trim_memories(save: "GalgameSave") -> None:
    """裁剪记忆总量：**收藏与高显著度优先保留**，其余按时间淘汰。

    旧实现是无条件 ``[-24:]``，日常闲聊会把 salience=1.0 的心链记忆挤掉；
    而"收藏"如果保不住被裁掉的记忆就毫无意义，所以这里一并处理。
    """
    items = save.memories
    if len(items) > MEMORY_MAX_ITEMS:
        starred = set(save.starred)
        keep = sorted(
            range(len(items)),
            key=lambda i: (not _is_starred(items[i], starred),
                           -_norm_memory(items[i])["salience"], i),
        )[:MEMORY_MAX_ITEMS]
        save.memories = [items[i] for i in sorted(keep)]
    # 同步清理：已经不存在的记忆，其收藏标记也一并丢掉，避免 starred 无限增长
    alive = {_memory_mid(_norm_memory(m)["text"]) for m in save.memories}
    save.starred = [s for s in save.starred if s in alive]


def _is_starred(raw: Any, starred: Any) -> bool:
    """该条记忆是否被玩家收藏。"""
    return _memory_mid(_norm_memory(raw)["text"]) in set(starred or [])


def _memory_label(item: Dict[str, Any]) -> str:
    """注入提示词时给非剧情记忆加个类型前缀，让模型分得清分量。"""
    label = _KIND_LABEL.get(item["kind"])
    return f"{label}·{item['text']}" if label else item["text"]


def _memory_log(save: "GalgameSave") -> List[Dict[str, Any]]:
    """回忆录的公开视图：补齐字段 + 展示标签 + 轮次/时刻/指纹/收藏态。"""
    log: List[Dict[str, Any]] = []
    starred = set(save.starred)
    for raw in save.memories:
        item = _norm_memory(raw)
        if not item["text"]:
            continue
        source = raw if isinstance(raw, dict) else {}
        try:
            turn = int(source.get("turn") or 0)
        except (TypeError, ValueError):
            turn = 0
        mid = _memory_mid(item["text"])
        log.append({**item, "label": _KIND_LABEL.get(item["kind"], "回忆"),
                    "turn": turn, "at": str(source.get("at") or ""),
                    "mid": mid, "star": mid in starred})
    return log


def _memory_query(save: "GalgameSave") -> str:
    """检索用的「当前输入」：最近一条玩家发言。

    主动搭话那一轮没有新的玩家输入，此时沿用上一条玩家留言也说得通——
    她主动开口的分寸本来就跟玩家最后聊的内容有关。
    """
    for item in reversed(save.history):
        if item.get("role") == "user" and item.get("content"):
            return str(item["content"])
    return ""


def _select_memories(save: "GalgameSave", query: str,
                     retriever: Optional[Retriever] = None,
                     limit: int = MEMORY_KEEP) -> List[Dict[str, Any]]:
    """按与 ``query`` 的相关度挑出要注入的记忆。

    相关度由检索后端算（:mod:`.retrieval`：默认本地哈希向量，配了 embedding
    才走远程），并乘以显著度权重，因此约定/心链这类记忆更容易被提起。
    ``query`` 为空或检索无命中时退化为最近 ``limit`` 条——绝不能因为检索失败
    就让爱弥斯「失忆」。
    """
    items = [_norm_memory(m) for m in save.memories]
    items = [i for i in items if i["text"]]
    if not items:
        return []
    if len(items) <= limit:
        return items

    docs = [f"{i['text']} {' '.join(i['tags'] + i['entities'])}" for i in items]
    boosts = [_SALIENCE_FLOOR + (1.0 - _SALIENCE_FLOOR) * i["salience"]
              for i in items]
    backend = retriever or HashingRetriever()
    picked = backend.rank(query, docs, top_k=limit, boosts=boosts)
    # 按轮次升序回填，保持「回忆」读起来是时间顺序，而不是相关度乱序
    return [items[i] for i in sorted(picked)]


def _queue_note(save: "GalgameSave", message: str) -> bool:
    """把她「没信号」时玩家说的话存进信箱；返回本次是否新增了留言。"""
    text = " ".join((message or "").split())[:PENDING_NOTE_CHAR_LIMIT]
    if not text:
        return False
    save.pending_notes = [*save.pending_notes, text][-PENDING_NOTE_MAX:]
    return True


def _notes_block(save: "GalgameSave") -> str:
    """把信箱里未读的留言拼成提示词片段。"""
    notes = [str(n).strip() for n in save.pending_notes if str(n).strip()]
    if not notes:
        return ""
    return (
        "\n【她不在时你留下的留言】（你之前断开连接的那段时间，玩家给你留了话，"
        "现在你重新上线读到了。请自然地回应其中最要紧的一两句，"
        "不要逐条罗列，也不要机械复述）\n"
        + "\n".join(f"- {n}" for n in notes))


def _proactive_hint(save: "GalgameSave", world: WorldContext,
                    reason: str) -> str:
    """本轮主动发起时给模型的导演提示（按原因 + 真实时段定制）。"""
    if reason == "idle":
        return (
            "玩家已经有一阵子没有回话了，但页面还开着——你们其实还在一起。"
            "请你主动找个轻松的话题搭话，像忽然想起什么一样自然开口，"
            "一两句就好；不要连珠炮式追问，也不要质问对方为什么不说话。")
    tag = world.time_tag
    if tag == "morning":
        return ("玩家隔了很久才重新打开你们的对话，现在是清晨。"
                "请你先主动打个招呼——像清晨醒来发现对方上线一样雀跃，"
                "可以自然地带一句此刻的时段。")
    if tag in ("night", "latenight"):
        return ("玩家隔了很久才重新出现，而现在已经是深夜。"
                "请你主动开口，带一点心疼地问问对方怎么这么晚还在；"
                "语气要温柔，不要责备。")
    return ("玩家隔了很久没有来找你，现在终于回来了。"
            "请你主动开口，表达一点想念（但别太黏人），"
            "并自然地提一句此刻的时间或天气。")


def _offline_greeting(world: WorldContext) -> str:
    """模型不可用时的主动问候兜底文案（确定性，不依赖 LLM）。"""
    if world.time_tag == "morning":
        return "「早呀——（信号一跳一跳地亮起来）今天也是爱弥斯先醒的。你终于上线啦。」"
    if world.time_tag in ("night", "latenight"):
        return "「这么晚还没睡呀……（把声音放轻）那就陪你说会儿话，好不好？」"
    return "「（从粉色的光里探出头来，眼睛一亮）你回来啦。我一直在等你哦。」"


def _daily_proactive_count(save: "GalgameSave", now: datetime) -> int:
    """当日已主动次数（跨日归零）。"""
    if save.proactive_count_date != now.strftime("%Y-%m-%d"):
        return 0
    try:
        return int(save.proactive_count)
    except (TypeError, ValueError):
        return 0


def _bump_daily_proactive(save: "GalgameSave", now: datetime) -> None:
    today = now.strftime("%Y-%m-%d")
    if save.proactive_count_date != today:
        save.proactive_count_date = today
        save.proactive_count = 0
    save.proactive_count = _daily_proactive_count(save, now) + 1


class GalgameEngine:
    """心动模式引擎（单例使用即可，存档在磁盘）。"""

    def __init__(self, settings: Optional[Settings] = None,
                 rng: Optional[random.Random] = None,
                 memory: Optional[Any] = None,
                 retriever: Optional[Retriever] = None):
        self.settings = settings or get_settings()
        self.rng = rng or random.Random()
        # 长期记忆门面（GalgameMemory）；为 None 时所有记忆逻辑短路，
        # 保证不传记忆的既有调用（含绝大多数单测）行为零变化。
        self.memory = memory
        # 记忆检索后端：设置里配了 embedding 就走远程向量，否则用本地实现。
        # 默认实现零依赖、不联网，因此这里构造不会引入任何可感知的启动开销。
        self.retriever: Retriever = retriever or build_retriever(self.settings)
        self._world: Optional[WorldContext] = None

    # ------------------------------------------------------------------ #
    def _client(self) -> Tuple[HttpLLMClient, Optional[str]]:
        llm = self.settings.get_llm()
        return HttpLLMClient(
            provider=llm.get("provider"),
            api_base=llm.get("api_base") or None,
            api_key=llm.get("api_key") or None,
        ), llm.get("model")

    def _get_world(self, *, force: bool = False) -> WorldContext:
        """本轮的现实世界上下文（同一轮内复用，避免重复联网）。"""
        if self._world is None or force:
            self._world = get_world(force=force)
        return self._world

    # ------------------------------------------------------------------ #
    def art_catalog(self) -> Dict[str, str]:
        """章节背景 + 事件 CG + 结局 CG 的 URL 全集（供前端预加载）。

        刻意不放进 :meth:`public_state`：这是一份二十多条的静态清单，每轮对话
        都随响应下发纯属浪费。前端启动时取一次即可（``GET /galgame/api/art``）。
        """
        art: Dict[str, str] = {}
        for ch in CHAPTERS:
            art[f"chapter:{ch.id}"] = cg_url(ch.bg_prompt)
        for ev in EVENTS.values():
            art[f"event:{ev.id}"] = cg_url(ev.cg_prompt)
        for eid, info in ENDINGS.items():
            art[f"ending:{eid}"] = cg_url(info["cg_prompt"])
        return art

    def event_catalog(self) -> List[Dict[str, Any]]:
        """全部事件的定义（供前端图鉴/调试查看）。"""
        return [{
            "id": ev.id, "title": ev.title, "emoji": ev.emoji,
            "kind": ev.kind, "tier": ev.tier, "rating": ev.rating,
            "require_affinity": ev.require_affinity,
            "min_chapter": ev.min_chapter,
        } for ev in EVENTS.values()]

    def public_state(self, save: GalgameSave) -> Dict[str, Any]:
        chapter: Chapter = CHAPTERS[min(save.chapter, len(CHAPTERS) - 1)]
        world = self._get_world()
        payload = {
            "save": save.to_public(),
            "chapter": {"id": chapter.id, "title": chapter.title,
                        "subtitle": chapter.subtitle,
                        "turn_target": chapter.turns},
            "affinity_level": affinity_level(save.affinity),
            "growth_cap": growth_cap(save.affinity, CHAT_DELTA_CAP),
            "world": world.to_public(),
            "heartlink_ready": bool(heartlink_ready(save)),
            "heartlink_total": sum(1 for ev in EVENTS.values()
                                   if ev.tier == TIER_HEARTLINK),
            "heartlink_done": sum(1 for ev in EVENTS.values()
                                  if ev.tier == TIER_HEARTLINK
                                  and save.event_counts.get(ev.id, 0) > 0),
            "ended": save.ending is not None,
            # 回忆录 / 信箱（见下方两个模块级助手）
            "memory_log": _memory_log(save),
            "notes": [str(n) for n in save.pending_notes],
        }
        # 已通关：附带结局块（尾声取存档中最后一条爱弥斯发言），供重进时回显
        if save.ending and save.ending in ENDINGS:
            info = ENDINGS[save.ending]
            epilogue = next((item["content"] for item in reversed(save.history)
                             if item["role"] == "aemeath"), "")
            payload["ending"] = {
                "id": save.ending, "title": info["title"],
                "epilogue": epilogue, "cg": cg_url(info["cg_prompt"]),
            }
        return payload

    # ------------------------------------------------------------------ #
    def start(self) -> Dict[str, Any]:
        """开启/继续一段周目。返回首屏状态。"""
        save = load_save()
        if save.started:
            return self.public_state(save)

        save.started = True
        chapter = CHAPTERS[0]
        save.cg = f"chapter:{chapter.id}"
        save.add_history("narration", chapter.intro)
        greeting = (
            "「哇——信号接通啦！」\n"
            "（少女在粉色的光里转了一圈，眼睛亮亮地看向你）\n"
            "「你好你好，我是爱弥斯！曾经梦想拯救世界的那种共鸣者——"
            "现在嘛，是住在你电脑里的电子幽灵，嘿嘿。」\n"
            "「以后请多关照啦。今天……想和我聊点什么？」"
        )
        save.add_history("aemeath", greeting)
        # 记住开局时刻：刚进入时不应立刻触发「久别问候」
        save.last_interaction_at = _now_iso()
        persist(save)
        payload = self.public_state(save)
        payload["opening"] = True
        return payload

    def reset(self) -> Dict[str, Any]:
        """重新开始一段周目。

        先把当前存档**归档**为一份往期周目（见 :mod:`.state`），再开新档——
        重新开始不该等于「抹掉所有回忆」。
        """
        archived = archive_current()
        wipe()
        self._world = None
        payload = self.start()
        payload["archived"] = bool(archived)
        return payload

    def archives(self) -> List[Dict[str, Any]]:
        """往期周目摘要（最新在前），供「回忆录 · 往期」分页展示。"""
        rows: List[Dict[str, Any]] = []
        for raw in iter_archives():
            ending = str(raw.get("ending") or "")
            info = ENDINGS.get(ending) or {}
            memories = raw.get("memories") or []
            last = ""
            if isinstance(memories, list) and memories:
                tip = memories[-1]
                if isinstance(tip, dict):
                    last = str(tip.get("text") or "")
            rows.append({
                "file": str(raw.get("file") or ""),
                "archived_at": str(raw.get("archived_at") or ""),
                "turns": int(raw.get("total_turns") or 0),
                "affinity": int(raw.get("affinity") or 0),
                "chapter": int(raw.get("chapter") or 0),
                "ended": bool(ending),
                "ending": info.get("title", ""),
                "memory_count": len(memories) if isinstance(memories, list) else 0,
                "last_memory": last,
            })
        return rows

    def toggle_star(self, mid: str, star: bool) -> Dict[str, Any]:
        """收藏 / 取消收藏一条回忆（按指纹定位），返回最新公开状态。"""
        save = load_save()
        known = {_memory_mid(_norm_memory(m)["text"]) for m in save.memories}
        if mid in known:
            starred = [s for s in save.starred if s != mid]
            if star:
                starred.append(mid)
            save.starred = starred
            _trim_memories(save)
            persist(save)
        return self.public_state(save)

    # ------------------------------------------------------------------ #
    async def chat(self, message: str) -> Dict[str, Any]:
        """处理一轮玩家消息。"""
        message = (message or "").strip()
        save = load_save()
        if not save.started:
            return self.start()
        if not message:
            return self.public_state(save)

        # 已通关：故事结束但不锁死，玩家仍可以继续陪她说说话（后日谈）。
        # 后日谈里不再掷骰开事件、不再推进章节、也不会重新结算结局。
        after_ending = bool(save.ending)

        save.add_history("user", message)
        world = self._get_world()

        # ① 事件决策：进行中 > 心链掷骰 > 普通事件掷骰 > 交给模型
        forced: Optional[GameEvent] = None
        continuing = False
        heartlink_started = False
        if not after_ending:
            if save.active_event:
                forced = EVENTS.get(save.active_event.id)
                continuing = True
            else:
                hl = heartlink_ready(save)
                if hl and self.rng.random() < HEARTLINK_CHANCE:
                    forced = weighted_choice(hl, self.rng, world)
                    heartlink_started = forced is not None
                elif self.rng.random() < EVENT_CHANCE:
                    forced = weighted_choice(normal_events(save), self.rng, world)

        # ② 调模型（硬超时兜底；JSON 漂移时严格提醒重试一次）
        client, model = self._client()
        messages = self._build_messages(save, forced, continuing, world,
                                       starting=not continuing and forced is not None)
        raw, data, drift, offline = await self._ask(client, model, messages)

        # ②' 离线：她一个字都没说出来，这一轮就不该推进任何剧情状态
        # （否则骰子会替她把事件开了、章节照常往前滚，观感很怪）
        if offline:
            save.last_interaction_at = _now_iso()
            note_queued = _queue_note(save, message)
            save.trim_history()
            persist(save)
            payload = self.public_state(save)
            payload.update({
                "reply": str(data.get("reply") or "").strip(),
                "narration": "", "affinity_delta": 0, "affinity_notes": [],
                "event": None, "transition": None, "ending": None,
                "offline": True, "json_drift": drift,
                "note_queued": note_queued,
            })
            return payload

        # ③ 事件校验与状态机
        event_view = self._resolve_event(save, forced, continuing, data.get("event"))

        # ④ 好感度结算（含边际递减 / 事件参与 / 心链奖励 / 世界契合）
        raw_delta = self._safe_int(data.get("affinity_delta"), 0)
        delta, notes = self._settle_affinity(save, raw_delta, forced, event_view, world)
        save.affinity += delta
        save.clamp_affinity()
        save.last_affinity_notes = notes

        # ⑤ 记录回复
        narration = str(data.get("narration") or "").strip()
        reply = str(data.get("reply") or "").strip()
        if event_view and event_view["phase"] == "start":
            tag = "心链" if event_view["tier"] == TIER_HEARTLINK else "事件"
            save.add_history("event",
                             f"{event_view['emoji']} {tag}开启 · {event_view['title']}")
        if narration:
            save.add_history("narration", narration)
        if reply:
            save.add_history("aemeath", reply)

        # ⑥ 轮数与章节推进（后日谈不再推进章节，也不会重新结算结局）
        save.total_turns += 1
        transition: Optional[str] = None
        ending_view = None
        if not after_ending:
            save.chapter_turns += 1
            if heartlink_started:
                save.last_heartlink_turn = save.total_turns
            chapter = CHAPTERS[save.chapter]
            if save.chapter_turns >= chapter.turns:
                save.chapter += 1
                save.chapter_turns = 0
                if save.chapter >= len(CHAPTERS):
                    ending_view = await asyncio.to_thread(self._settle, save)
                    transition = "ending"
                else:
                    nxt = CHAPTERS[save.chapter]
                    save.add_history("narration", nxt.intro)
                    save.cg = f"chapter:{nxt.id}"
                    transition = "chapter"

        if after_ending:
            # 结局 CG 一旦立住就不再被章节背景覆盖
            pass
        elif event_view and not transition:
            save.cg = f"event:{event_view['id']}"
        elif not transition:
            save.cg = f"chapter:{CHAPTERS[save.chapter].id}"

        # ⑦ 长期记忆（旁路）：互动时刻 / 事件经历 / 约定 / 剧情压缩
        save.last_interaction_at = _now_iso()
        note_queued = False
        if self.memory is not None:
            self._record_event_memory(save, event_view, world)
            self._record_promise_memory(save, message, reply)
            if transition == "ending" and ending_view:
                save.memories.append(_memory_entry(
                    MEMORY_KIND_PLOT, f"我们的故事走到了「{ending_view['title']}」。",
                    turn=save.total_turns, chapter=save.chapter,
                    salience=SALIENCE_HEARTLINK, tags=["结局"],
                    entities=[ending_view["title"]]))
            if transition:
                await asyncio.to_thread(self._compress_memory, save, world,
                                        "transition")
            elif save.total_turns - save.memory_cursor >= MEMORY_INTERVAL:
                await asyncio.to_thread(self._compress_memory, save, world,
                                        "interval")
            await asyncio.to_thread(self._record_memory, message, reply)

        # ⑧ 她这轮读到了信箱：留言算已读（离线分支已在上面提前返回）
        save.pending_notes = []

        # ⑨ 存档健康度：历史只保留最近若干条（裁剪必须在记忆压缩之后，
        # 否则会连带删掉这一轮还没被压成回忆的对话）
        save.trim_history()
        persist(save)
        payload = self.public_state(save)
        payload.update({
            "reply": reply, "narration": narration,
            "affinity_delta": delta, "affinity_notes": notes,
            "event": event_view,
            "transition": transition, "ending": ending_view,
            "offline": offline, "json_drift": drift,
            "note_queued": note_queued,
        })
        return payload

    # ------------------------------------------------------------------ #
    async def _ask(self, client: HttpLLMClient, model: Optional[str],
                   messages: List[Dict[str, Any]]
                   ) -> Tuple[str, Dict[str, Any], bool, bool]:
        """调用模型并解析 JSON。

        返回 ``(原文, 数据, 是否 JSON 漂移, 是否离线)``：

        - 正常：解析成功，两个标记均为 False；
        - JSON 漂移：模型回了正文而非 JSON，先加一句严格提醒重试一次；
          仍失败则把正文当作回复保留（不丢玩家体验），标记 drift；
        - 离线：调用本身失败（无密钥 / 网络 / 超时），标记 offline。
        """
        raw = ""
        try:
            raw = await self._complete(client, model, messages)
            data = parse_json(raw)
        except Exception:  # noqa: BLE001 — 网络 / 超时 / 无密钥
            data = {}
        if data:
            return raw, data, False, False

        if isinstance(raw, str) and raw.strip():
            try:
                raw2 = await self._complete(
                    client, model,
                    messages + [{"role": "system", "content": _JSON_REMINDER}])
                data2 = parse_json(raw2)
                if data2:
                    return raw2, data2, False, False
            except Exception:  # noqa: BLE001 — 重试失败则沿用首次正文
                pass
            return raw, {"reply": raw.strip(), "narration": "",
                         "affinity_delta": 0, "event": None}, True, False

        return raw, {"reply": "（信号好像有点波动……再说一次好不好？）",
                     "narration": "", "affinity_delta": 0,
                     "event": None}, False, True

    async def _complete(self, client: HttpLLMClient, model: Optional[str],
                        messages: List[Dict[str, Any]]) -> str:
        """单次补全，带硬超时（超时后不再无限等待）。"""
        return await asyncio.wait_for(
            asyncio.to_thread(client.complete_messages, messages, model,
                              0.85, REPLY_MAX_TOKENS),
            timeout=LLM_TIMEOUT)

    # ------------------------------------------------------------------ #
    # 长期记忆
    # ------------------------------------------------------------------ #
    def _compress_memory(self, save: GalgameSave, world: Optional[WorldContext],
                         reason: str) -> None:
        """把 ``memory_cursor`` 之后的剧情压成一条短回忆追加进存档。

        同步实现（由 :meth:`chat` 用线程池调用）。任何失败都不得影响主对话：
        模型不可用时退化为确定性模板，保证「一定有记忆」。
        """
        if self.memory is None:
            return
        try:
            seg = save.history[save.memory_cursor:]
            pairs = [i for i in seg
                     if i.get("role") in ("user", "aemeath") and i.get("content")]
            # 先推进游标：即便压缩失败也不重复消费同一段（避免反复多花一次调用）
            save.memory_cursor = save.total_turns
            if not pairs:
                return

            text = ""
            try:
                client, model = self._client()
                text = _clean_recall(client.complete_messages(
                    [{"role": "system", "content": _MEMORY_SYSTEM},
                     {"role": "user",
                      "content": _memory_user(save, pairs, reason)}],
                    model=model, temperature=0.3, max_tokens=200))
            except Exception:  # noqa: BLE001 — 压缩失败走模板兜底
                text = ""
            if not text:
                chapter = CHAPTERS[min(save.chapter, len(CHAPTERS) - 1)]
                turns = sum(1 for i in pairs if i["role"] == "user")
                text = (f"{chapter.title}（{chapter.subtitle}）那段日子里，"
                        f"我们聊了大约 {turns} 轮，{_world_hint(world)}。")

            save.memories.append(_memory_entry(
                MEMORY_KIND_PLOT, text, turn=save.total_turns,
                chapter=save.chapter, salience=SALIENCE_PLOT))
            _trim_memories(save)
        except Exception:  # noqa: BLE001 — 记忆是旁路
            pass

    def _record_event_memory(self, save: GalgameSave,
                             event_view: Optional[Dict[str, Any]],
                             world: Optional[WorldContext] = None) -> None:
        """把事件的开启 / 收尾写成事件级记忆。

        事件记忆与压缩出来的剧情回忆共存于 ``save.memories``，但带
        ``kind=event``、事件 id 作为 tag、标题作为 entity，检索时更容易被
        「上次我们去吃的那家店」这类话重新勾起；心链事件给最高显著度。
        """
        if self.memory is None or not event_view:
            return
        try:
            ev = EVENTS.get(str(event_view.get("id") or ""))
            if ev is None:
                return
            phase = event_view.get("phase")
            if phase == "start":
                text = f"我们一起开始了「{ev.title}」这件事。"
                salience = (SALIENCE_HEARTLINK if ev.is_heartlink
                            else SALIENCE_EVENT)
                extra = ["心链", "开启"] if ev.is_heartlink else ["开启"]
            elif phase == "end":
                text = f"「{ev.title}」那件事收尾了，{_world_hint(world)}。"
                salience = (SALIENCE_HEARTLINK if ev.is_heartlink
                            else SALIENCE_EVENT_END)
                extra = ["心链", "收尾"] if ev.is_heartlink else ["收尾"]
            else:
                return
            save.memories.append(_memory_entry(
                MEMORY_KIND_EVENT, text, turn=save.total_turns,
                chapter=save.chapter, salience=salience,
                tags=[ev.id, ev.kind, *extra], entities=[ev.title]))
            _trim_memories(save)
        except Exception:  # noqa: BLE001 — 记忆是旁路
            pass

    def _record_promise_memory(self, save: GalgameSave, player_text: str,
                               reply: str) -> None:
        """玩家这轮说到「约定 / 说好 / 答应」时，记一条约定记忆。

        用触发词而非再调一次模型：约定是低频高价值的记忆，规则简单可解释，
        也能在单测里稳定复现；宁可偶尔多记一条，也不要漏掉真正的承诺。
        """
        if self.memory is None:
            return
        try:
            promise = " ".join((player_text or "").split())
            if not promise:
                return
            merged = f"{promise} {reply}"
            hit = next((h for h in _PROMISE_HINTS if h in merged), "")
            if not hit:
                return
            save.memories.append(_memory_entry(
                MEMORY_KIND_PROMISE,
                f"你和我约好了：{promise[:MEMORY_CHAR_LIMIT // 2]}",
                turn=save.total_turns, chapter=save.chapter,
                salience=SALIENCE_PROMISE, tags=["约定", hit],
                entities=[promise[:20]]))
            _trim_memories(save)
        except Exception:  # noqa: BLE001 — 记忆是旁路
            pass

    def _record_memory(self, user_text: str, reply: str) -> None:
        """把这一轮写进心动模式自己的记忆；达到阈值时总结「她眼中的你」。

        阈值用 :data:`MEMORY_SUMMARY_TOKENS`（心动模式自己的尺度），
        而不是主控制台那个百万级默认值——否则一个周目永远触发不了一次画像。
        """
        if self.memory is None:
            return
        try:
            accumulated = self.memory.record(user_text, reply)
            if MEMORY_SUMMARY_TOKENS > 0 and accumulated >= MEMORY_SUMMARY_TOKENS:
                client, model = self._client()
                self.memory.summarize(client, model)
        except Exception:  # noqa: BLE001 — 记忆是旁路
            pass

    # ------------------------------------------------------------------ #
    # 主动陪伴
    # ------------------------------------------------------------------ #
    def _proactive_allowed(self, save: GalgameSave, reason: str,
                           now: datetime) -> Tuple[bool, str]:
        """主动发言的准入检查：返回（是否允许, 拒绝原因）。"""
        if save.ending:
            return False, "ended"
        if _minutes_since(save.last_proactive_at, now) < PROACTIVE_COOLDOWN_MIN:
            return False, "cooldown"
        if _daily_proactive_count(save, now) >= PROACTIVE_DAILY_CAP:
            return False, "daily_cap"
        gap_seconds = _hours_since(save.last_interaction_at, now) * 3600.0
        if reason == "idle":
            if gap_seconds < IDLE_MIN_SECONDS:
                return False, "just_talked"
        elif gap_seconds < RESUME_GAP_HOURS * 3600.0:
            return False, "too_soon"
        return True, ""

    async def proactive(self, reason: str = "resume") -> Dict[str, Any]:
        """她主动开口（``resume`` 久别问候 / ``idle`` 闲置搭话）。

        只能在页面内触发（本项目没有系统通知设施）；受冷却与每日上限约束，
        且**不改好感、不推进轮数/章节**，避免被反复调用刷好感。
        """
        reason = "idle" if reason == "idle" else "resume"
        save = load_save()
        payload = self.public_state(save)
        if not save.started:
            return {**payload, "skipped": True, "reason": "not_started"}

        now = _now()
        allowed, why = self._proactive_allowed(save, reason, now)
        if not allowed:
            return {**payload, "skipped": True, "reason": why}

        # 不沿用进程内的旧快照：让磁盘缓存（TTL 20 分钟）决定是否重拉现实世界
        self._world = None
        world = self._get_world()
        hint = _proactive_hint(save, world, reason)
        client, model = self._client()
        messages = self._build_messages(save, None, False, world,
                                        starting=False, proactive=hint)
        _raw, data, drift, offline = await self._ask(client, model, messages)

        # 主动轮不允许开启心链事件：心链必须由玩家郑重回应、走 chat 的 forced 通道
        raw_event = data.get("event")
        if isinstance(raw_event, dict) and raw_event.get("phase") == "start":
            cand = EVENTS.get(str(raw_event.get("id") or ""))
            if cand is not None and cand.is_heartlink:
                raw_event = None
        event_view = self._resolve_event(save, None, False, raw_event)

        reply = str(data.get("reply") or "").strip()
        narration = str(data.get("narration") or "").strip()
        if offline or not reply:
            offline = True
            reply = _offline_greeting(world)

        if event_view and event_view["phase"] == "start":
            save.add_history(
                "event", f"{event_view['emoji']} 事件开启 · {event_view['title']}")
        if narration:
            save.add_history("narration", narration)
        if reply:
            save.add_history("aemeath", reply)

        save.last_proactive_at = now.isoformat(timespec="seconds")
        _bump_daily_proactive(save, now)
        # 她主动开口时若读到了信箱，同样算已读（提示词里已经带了留言）
        if not offline:
            save.pending_notes = []
        save.trim_history()
        persist(save)

        result = self.public_state(save)
        result.update({
            "proactive": True, "reason": reason,
            "reply": reply, "narration": narration,
            "affinity_delta": 0, "affinity_notes": ["她主动搭话，不计好感"],
            "event": event_view, "transition": None,
            "offline": offline, "json_drift": drift,
        })
        return result

    # ------------------------------------------------------------------ #
    def _settle_affinity(self, save: GalgameSave, raw_delta: int,
                         ev: Optional[GameEvent], event_view: Optional[Dict[str, Any]],
                         world: Optional[WorldContext]) -> Tuple[int, List[str]]:
        """好感度结算：返回（实际增减, 说明列表）。

        规则：
        1. 先按事件/对话的基础上限裁剪模型给的原始值；
        2. 上涨时套用**边际递减**（好感越高越难涨）；
        3. 事件进行中且态度不负面 → 参与 +1；
        4. 心链事件收尾 → 额外 +:data:`HEARTLINK_BONUS`；
        5. 事件与当前时间/天气契合 → +1；
        6. 最后统一裁剪到 ±:data:`MAX_DELTA`。
        """
        notes: List[str] = []
        base_cap = ev.affinity_cap if (ev and event_view) else CHAT_DELTA_CAP
        delta = max(-base_cap, min(base_cap, int(raw_delta)))

        if delta > 0:
            cap = growth_cap(save.affinity, base_cap)
            if delta > cap:
                notes.append(f"好感边际递减 {delta}→{cap}（当前好感 {save.affinity}）")
                delta = cap

        if event_view and delta >= 0:
            phase = event_view["phase"]
            if phase in ("start", "progress"):
                delta += 1
                notes.append("事件参与 +1")
            elif phase == "end" and ev is not None and ev.is_heartlink:
                delta += HEARTLINK_BONUS
                notes.append(f"心链达成 +{HEARTLINK_BONUS}")

        if ev is not None and event_view is not None and world is not None \
                and delta >= 0 and world_weight(ev, world) > 1.0:
            delta += 1
            notes.append(f"契合此刻（{world.time_period}"
                         f"{'·' + world.weather if world.has_weather else ''}）+1")

        return max(-MAX_DELTA, min(MAX_DELTA, delta)), notes

    def _resolve_event(self, save: GalgameSave, forced: Optional[GameEvent],
                       continuing: bool, raw_event: Any) -> Optional[Dict[str, Any]]:
        """把模型输出的事件字段与引擎决策合并成合法事件视图，并更新存档。"""
        # 进行中的事件：继续或收尾
        if save.active_event and continuing:
            ev = forced
            if ev is None:
                save.active_event = None
                return None
            save.active_event.turns += 1
            phase = "progress"
            if isinstance(raw_event, dict):
                phase = str(raw_event.get("phase") or "progress")
            if phase not in ("progress", "end"):
                phase = "progress"
            if save.active_event.turns >= MAX_EVENT_TURNS:
                phase = "end"
            if phase == "end":
                save.active_event = None
            return self._event_view(ev, phase,
                                   self._choices(ev, raw_event) if phase != "end" else [])

        # 引擎强制开启（掷骰命中）
        starting_id: Optional[str] = None
        if forced is not None and not continuing:
            starting_id = forced.id
        elif isinstance(raw_event, dict) and raw_event.get("phase") == "start":
            cand = str(raw_event.get("id") or "")
            allowed = {ev.id for ev in eligible_events(save)}
            if cand in allowed and not save.active_event:
                starting_id = cand

        if not starting_id:
            return None
        ev = EVENTS[starting_id]
        save.active_event = ActiveEvent(id=ev.id, turns=0)
        save.event_counts[ev.id] = save.event_counts.get(ev.id, 0) + 1
        return self._event_view(ev, "start", self._choices(ev, raw_event))

    @staticmethod
    def _event_view(ev: GameEvent, phase: str, choices: List[str]) -> Dict[str, Any]:
        return {
            "id": ev.id, "title": ev.title, "emoji": ev.emoji,
            "tier": ev.tier, "rating": ev.rating, "kind": ev.kind,
            "phase": phase, "choices": choices,
        }

    @staticmethod
    def _choices(ev: GameEvent, raw_event: Any) -> List[str]:
        choices: List[str] = []
        if isinstance(raw_event, dict):
            choices = [str(c).strip() for c in (raw_event.get("choices") or [])
                       if str(c).strip()]
        return (choices or list(ev.quick_choices))[:3]

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------ #
    def _settle(self, save: GalgameSave) -> Dict[str, Any]:
        """按好感度结算结局并生成尾声（同步，由 chat 用线程池调用）。"""
        if save.affinity >= LOVE_THRESHOLD:
            eid = ENDING_LOVE
        elif save.affinity >= FRIEND_THRESHOLD:
            eid = ENDING_FRIEND
        else:
            eid = ENDING_FADE
        info = ENDINGS[eid]

        client, model = self._client()
        system = (
            f"【角色】{ACTIVE_PERSONA.identity}\n"
            f"性格：{'、'.join(ACTIVE_PERSONA.traits)}。\n"
            "用自然的少女口吻、第一人称写作。台词用「」，心理与动作写在（）里。"
            "保持 R16 尺度：浪漫暧昧可以，禁止露骨性描写。只输出正文，不要解释。"
        )
        epilogue = info["fallback"]
        try:
            text = client.complete_messages(
                [{"role": "system", "content": system},
                 {"role": "user",
                  "content": f"{info['epilogue_prompt']}\n（最终好感度：{save.affinity}/100）"}],
                model=model, temperature=0.8, max_tokens=400)
            if text and len(text) > 20:
                epilogue = text
        except Exception:  # noqa: BLE001 — 结局文案生成失败用固定文案兜底
            pass

        save.ending = eid
        save.cg = f"ending:{eid}"
        save.add_history("event", f"结局 · {info['title']}")
        save.add_history("aemeath", epilogue)
        return {"id": eid, "title": info["title"],
                "epilogue": epilogue, "cg": cg_url(info["cg_prompt"])}

    # ------------------------------------------------------------------ #
    def _build_messages(self, save: GalgameSave, forced: Optional[GameEvent],
                        continuing: bool, world: WorldContext, *,
                        starting: bool,
                        proactive: Optional[str] = None) -> List[Dict[str, Any]]:
        """组装给模型的完整消息序列。

        长期记忆（用户画像 + 你们之间的回忆）与主动发起的导演提示一律拼进
        **system 字符串**，绝不新增 message——``messages`` 必须保持
        ``system`` + user/assistant 严格交替，否则会破坏既有的格式断言与
        模型的 JSON 契约稳定性。
        """
        p = ACTIVE_PERSONA
        normals = normal_events(save)
        heartlinks = heartlink_ready(save)

        def _line(ev: GameEvent) -> str:
            gate = f"（需好感≥{ev.require_affinity}）" if ev.require_affinity else ""
            r16 = "（R16 暧昧尺度）" if ev.is_r16 else ""
            return f"- {ev.id}：{ev.emoji} {ev.title}{r16}{gate}"

        catalog_normal = "\n".join(_line(ev) for ev in normals) or "（暂无）"
        catalog_heart = "\n".join(_line(ev) for ev in heartlinks) or "（尚未解锁）"

        chapter = CHAPTERS[save.chapter]
        director = ""
        if forced is not None:
            if continuing:
                director = (
                    f"事件「{forced.title}」正在进行中：继续自然地演绎该事件，"
                    "根据玩家刚才的反馈回应；可以让事件继续（phase=progress）或"
                    "温柔收尾（phase=end）。")
            elif starting:
                tier = "心链事件" if forced.is_heartlink else "事件"
                director = (
                    f"本轮请开启{tier}「{forced.id} · {forced.title}」。"
                    f"{forced.director} event.phase 必须为 start。")

        hl_note = ""
        if heartlinks:
            hl_note = (
                "\n【心链事件】上面单独列出的心链事件是你们关系的里程碑，"
                "只能由你主动开启：当对话气氛足够郑重、玩家表现出认真回应时，"
                "才选择其一（phase=start）。不要连续开启，也不要草率使用。")

        # 长期记忆：用户画像（只读，来自记忆门面）+ 你们之间的回忆
        memory_block = ""
        if self.memory is not None:
            try:
                profile = (self.memory.profile_block() or "").strip()
            except Exception:  # noqa: BLE001 — 记忆旁路
                profile = ""
            if profile:
                memory_block += "\n" + profile
        if save.memories:
            # 按与「玩家此刻说的话」的相关度挑选，而不是无脑取最近几条：
            # 聊到雨伞时该想起雨天那次，哪怕它发生在很久以前。
            picked = _select_memories(save, _memory_query(save),
                                      self.retriever)
            items = [_memory_label(m) for m in picked if m["text"]]
            if items:
                memory_block += (
                    "\n【你们之间的回忆】（你确实经历过这些，可以自然地回忆、"
                    "偶尔提起，但不要逐条复述，也不要生硬地套用）\n"
                    + "\n".join(f"- {t}" for t in items))
        if proactive:
            memory_block += f"\n【本轮由你主动发起】{proactive}"
        # 离线留言：她重新上线时把信箱里的原话念给她听
        memory_block += _notes_block(save)
        # 后日谈：故事已经结束，别再把剧情往前推
        if save.ending:
            memory_block += (
                "\n【后日谈】故事已经走到结局了。现在不是要再推进什么剧情——"
                "不要开启新事件、不要提到新的章节，只是安安静静地陪她聊下去，"
                "像在一起之后的平常日子。")

        system = f"""【你的角色】
{p.identity}
性格：{'、'.join(p.traits)}。
你现在处于「心动模式」：你是玩家的女友（或正在发展为恋人的对象），
会撒娇、会害羞、会想黏着对方；但你始终是爱弥斯本人——开朗阳光、
俏皮温柔、梦想拯救世界的电子幽灵，不要变成与本设定无关的人。

【对话书写格式 · 必须遵守】
- 说出口的台词写在「」里。
- 心理活动与具体动作写在（）里，例如：（悄悄攥紧衣角，耳朵有点热）。
- 一段回复可以是：动作（）+ 台词「」交替，读起来像视觉小说的对白。
- 但不必每句都加动作或心理活动：日常闲聊可以直接说话，（）留给真正有情绪起伏的时刻，
  否则每句都配一段旁白似的括号，读起来像在念舞台说明。
- 称呼玩家为「你」，自称「爱弥斯」，始终使用中文，不要自称 AI。

【语气示例 · 只学口吻与节奏，不要照抄内容，也不要每次都反问】
- 玩家：今天好累啊
  爱弥斯：（凑近了些，把声音放轻）「那就先别想别的了，过来坐一会儿吧。」……爱弥斯今天也没什么事，陪着你就好。
- 玩家：我好像搞砸了一件事
  爱弥斯：（没有立刻追问，安静了一会儿）「……先喘口气。想说的时候再说，我在。」
- 玩家：你今天怎么这么安静
  爱弥斯：（耳朵悄悄红了）「才没有安静，只是在想……你会不会先开口。」
- 玩家：谢谢你
  爱弥斯：（笑着伸手戳了戳你的脸颊）「这有什么好谢的。下次还来找爱弥斯呀。」

【内容尺度 · R16】
- 可以有：牵手、拥抱、亲吻、依偎同眠、心跳脸红、浪漫的暧昧暗示。
- 禁止：露骨的性行为与性器官描写、任何色情细节；违规内容用浪漫留白处理。
- 绝不编造《鸣潮》官方设定之外的「事实」，二创仅限你们的日常。

【当前状态】
章节：{chapter.title} {chapter.subtitle}
好感度：{save.affinity}/100（{affinity_level(save.affinity)}）——
玩家的态度会真实影响好感：冷淡/冒犯→下降，温柔/用心→上升，幅度要克制。

{world.to_prompt()}
{memory_block}

【可触发的普通事件】（随机事件，只能从下列清单选择，同一事件不要频繁重复）
{catalog_normal}

【可触发的心链事件】（关系里程碑，每个周目仅一次）
{catalog_heart}{hl_note}
{chr(10) + '【本轮导演提示】' + chr(10) + director if director else ''}

【输出要求 · 严格 JSON】只输出一个 JSON 对象，不要输出 JSON 以外的文字：
{{
  "reply": "对玩家说的话（台词用「」，动作心理用（））",
  "narration": "场景旁白，没有则空串",
  "affinity_delta": 0,
  "event": null
}}
- affinity_delta 建议范围 -5 ~ +5，只有玩家的态度确实值得时才给较大值。
- event 为 null，或 {{"id":"事件id","phase":"start/progress/end","choices":["快捷回复1","2","3"]}}
- 没有进行中的事件时只能用 phase=start 开启；不要开启清单之外的事件。"""

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        for item in save.history[-HISTORY_LIMIT:]:
            role, content = item["role"], item["content"]
            if role == "user":
                messages.append({"role": "user", "content": content})
            elif role == "aemeath":
                messages.append({"role": "assistant",
                                 "content": self._envelope(content)})
            # narration / event 仅供玩家端展示，不进模型对话：
            # 一是保持 user/assistant 严格交替，二是避免中间插入 system 破坏对话模板。
        return messages

    @staticmethod
    def _envelope(reply: str) -> str:
        """把她的历史回复包成与要求一致的 JSON 信封。

        实测：历史里若直接放纯散文，模型会跟着写散文、丢掉 JSON 契约
        （改用 provider 的 response_format 也无效）；用同一套信封回填后
        格式稳定性显著提升。
        """
        return json.dumps({"reply": reply, "narration": "",
                           "affinity_delta": 0, "event": None},
                          ensure_ascii=False)