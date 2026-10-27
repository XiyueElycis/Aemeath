"""心动模式 · 存档状态与持久化。

存档落在 ``~/.aemeath_agent/galgame/save.json``：好感度、章节、对话历史、
事件记录与结局。单用户本地单机使用，读写均为原子写（先写临时文件再替换）。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 好感度区间
MIN_AFFINITY = 0
MAX_AFFINITY = 100

# 结局 id
ENDING_LOVE = "love"      # 真爱相守
ENDING_FRIEND = "friend"  # 挚友温柔
ENDING_FADE = "fade"      # 幽灵离别

# 记忆条目类型。存档里早期的回忆没有 kind 字段，一律按 MEMORY_KIND_PLOT 处理。
MEMORY_KIND_PLOT = "plot"        # 剧情片段：一段对话压缩成的回忆
MEMORY_KIND_EVENT = "event"      # 事件经历：随机事件 / 心链事件的开启与收尾
MEMORY_KIND_PROMISE = "promise"  # 约定承诺：双方说好的事


def save_dir() -> Path:
    """存档目录（可用 AEMEATH_AGENT_HOME 风格环境变量覆盖基目录）。"""
    base = os.environ.get("AEMEATH_AGENT_DATA_HOME") or os.path.expanduser("~/.aemeath_agent")
    return Path(base) / "galgame"


def save_path() -> Path:
    return save_dir() / "save.json"


def memory_dir() -> Path:
    """心动模式专属记忆目录（与主控制台会话画像隔离，避免互相污染）。"""
    return save_dir() / "memory"


# 存档里保留的历史条数上限。
# 每轮写入 2~4 条（玩家、她、旁白、事件行），而注入模型只看最近 26 条；
# 不设上限的话 save.json 会随着周目一路膨胀，而每轮对话都要整份读+写。
# 长期连续性由 memories（回忆）承担，历史只是「最近的现场」。
HISTORY_MAX_ITEMS = 200


def archive_dir() -> Path:
    """往期周目目录：重新开始时旧存档归档到这里，而不是直接删掉。"""
    return save_dir() / "archive"


def _now_iso() -> str:
    """本地时间戳（秒级）。

    刻意不带时区：玩家看到的「昨天 / 今天」就是本机时间，前端也按本地时间
    解析；存档只在本机单用户使用，不需要跨时区可比性。
    """
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class ActiveEvent:
    """进行中的事件。"""

    id: str
    turns: int = 0          # 事件已持续的对话轮数


@dataclass
class GalgameSave:
    """一份心动模式存档。"""

    started: bool = False
    affinity: int = 20                 # 0–100，初始略带好感
    chapter: int = 0                   # 当前章节下标
    chapter_turns: int = 0             # 本章已进行轮数
    total_turns: int = 0
    # 对话历史：{role: user/aemeath/narration/event, content: str, at: "YYYY-MM-DDTHH:MM:SS"}
    # ``at`` 是写入时刻，供前端画时间轴分隔线；老存档没有这个键，前端会跳过分隔线。
    history: List[Dict[str, str]] = field(default_factory=list)
    event_counts: Dict[str, int] = field(default_factory=dict)  # 事件 id -> 已触发次数
    active_event: Optional[ActiveEvent] = None
    ending: Optional[str] = None       # 已结算结局 id
    cg: Optional[str] = None           # 当前 CG/背景 key
    # 最近一次心链事件发生的轮次（-1 表示尚未发生），用于心链冷却
    last_heartlink_turn: int = -1
    # 本轮好感度变化说明（供前端/思考面板展示，不参与结算）
    last_affinity_notes: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # 长期记忆：剧情回忆（跨 26 条历史窗口与重启的连续感）
    # ------------------------------------------------------------------ #
    # 元素形如 {"turn": int, "chapter": int, "text": str, "at": "YYYY-MM-DD HH:MM:SS"}
    memories: List[Dict[str, Any]] = field(default_factory=list)
    # 已压缩到 total_turns 的哪一轮，避免重复压缩同一段剧情
    memory_cursor: int = 0

    # ------------------------------------------------------------------ #
    # 主动陪伴：她主动找你说话的频控与时间锚点
    # ------------------------------------------------------------------ #
    last_interaction_at: str = ""      # 最后一次「玩家发言」时间（ISO）
    last_proactive_at: str = ""        # 最后一次「她主动发言」时间（ISO）
    proactive_count: int = 0           # 当日已主动次数
    proactive_count_date: str = ""     # "YYYY-MM-DD"，跨日归零

    # ------------------------------------------------------------------ #
    # 离线留言：模型不可用时玩家说的话，先替她记下，恢复后一次性读给她
    # ------------------------------------------------------------------ #
    pending_notes: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # 回忆收藏：存记忆正文的指纹（见 engine._memory_mid）。
    # 存指纹而非下标，是因为记忆会被裁剪、下标会漂移。
    # ------------------------------------------------------------------ #
    starred: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def clamp_affinity(self) -> None:
        self.affinity = max(MIN_AFFINITY, min(MAX_AFFINITY, int(self.affinity)))

    def add_history(self, role: str, content: str, at: str = "") -> None:
        content = (content or "").strip()
        if content:
            self.history.append({"role": role, "content": content,
                                 "at": at or _now_iso()})

    def to_public(self) -> Dict[str, Any]:
        """返回给前端的公开视图。"""
        return asdict(self)

    def trim_history(self, limit: int = HISTORY_MAX_ITEMS) -> int:
        """把历史裁到 ``limit`` 条以内，返回丢掉的头条数。

        两个要点：
        1. 尽量从**玩家发言**处下刀，避免历史从一个半截的旁白或她的半句台词开始；
        2. ``memory_cursor`` 同时被当作历史下标使用（见 :meth:`GalgameEngine
           ._compress_memory`），所以必须同步前移，否则裁剪后会把已压缩过的
           剧情重新压一遍。
        """
        overflow = len(self.history) - limit
        if overflow <= 0:
            return 0
        drop = overflow
        while drop < len(self.history) - 1 and \
                self.history[drop].get("role") != "user":
            drop += 1
        if drop >= len(self.history):
            drop = overflow
        self.history = self.history[drop:]
        self.memory_cursor = max(0, int(self.memory_cursor) - drop)
        return drop


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #
def load_save() -> GalgameSave:
    """读取存档；不存在或损坏时返回新存档。"""
    path = save_path()
    if not path.exists():
        return GalgameSave()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        active = data.pop("active_event", None)
        save = GalgameSave(**{k: v for k, v in data.items()
                              if k in GalgameSave.__dataclass_fields__})
        if isinstance(active, dict) and active.get("id"):
            save.active_event = ActiveEvent(
                id=str(active["id"]), turns=int(active.get("turns", 0)))
        save.clamp_affinity()
        return save
    except (ValueError, TypeError, OSError):
        return GalgameSave()


def persist(save: GalgameSave) -> None:
    """原子写存档。"""
    path = save_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(asdict(save), ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def wipe() -> None:
    """删除存档。"""
    path = save_path()
    if path.exists():
        path.unlink()


# --------------------------------------------------------------------------- #
# 往期周目（归档）
# --------------------------------------------------------------------------- #
# 最多保留多少份归档；超出后按文件名（即时间）淘汰最旧的
ARCHIVE_KEEP = 10


def archive_current() -> Optional[Dict[str, Any]]:
    """把当前存档归档成一份「往期周目」。

    返回归档的原始数据（含 ``archived_at``）；没有可归档的存档时返回 None。
    重新开始不再等于「抹掉一切」——回忆留在归档里，玩家以后还能翻。
    """
    save = load_save()
    if not save.started:
        return None
    directory = archive_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = directory / f"save_{stamp}.json"
    # 同一秒内连续归档（测试、连点「重新开始」）会撞名，加序号避免互相覆盖
    seq = 1
    while path.exists():
        path = directory / f"save_{stamp}_{seq}.json"
        seq += 1
    payload = asdict(save)
    payload["archived_at"] = _now_iso()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    _trim_archives(directory)
    return payload


def _trim_archives(directory: Path, keep: int = ARCHIVE_KEEP) -> None:
    """只保留最近 ``keep`` 份归档（文件名按时间排序即时间序）。"""
    try:
        files = sorted(directory.glob("save_*.json"))
    except OSError:
        return
    for old in files[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass


def iter_archives() -> List[Dict[str, Any]]:
    """读取全部归档原始数据，最新的在前；损坏的归档跳过。"""
    directory = archive_dir()
    if not directory.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("save_*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data["file"] = path.name
            rows.append(data)
    return rows
