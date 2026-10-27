"""智能体长期记忆：对话累积 → 阈值触发 → LLM 总结 → 用户画像配置。

工作方式：
- 每轮对话由对话服务 :meth:`MemoryManager.record` 追加到滚动记录并估算 token；
- 自上次总结以来累积 token 达到阈值（默认 1,000,000，可在设置中改）时，
  :meth:`MemoryManager.summarize` 调用 LLM 对近期对话做总结，与既有画像合并，
  写入 ``user_profile.json``；随后滚动记录归档，计数归零；
- 后续对话通过 :func:`profile_block` 把画像注入 system prompt，爱弥斯据此
  自然地记住用户的喜好与爱好。

所有文件位于 ``~/.aemeath_agent/memory/``；构造时可注入目录（测试用）。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 滚动记录中总结后仍保留的近期对话条数
_KEEP_RECENT = 20
#: 画像各列表最大条数（防无限膨胀）
_LIST_CAP = 24

_CJK_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：CJK 按 1 字 ≈ 0.9 token，其余按 4 字符 ≈ 1 token。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return int(cjk * 0.9 + other / 4) + 1


@dataclass
class UserProfile:
    """用户画像（user_profile.json）。"""

    summary: str = ""
    likes: List[str] = field(default_factory=list)
    hobbies: List[str] = field(default_factory=list)
    dislikes: List[str] = field(default_factory=list)
    communication_style: str = ""
    facts: Dict[str, str] = field(default_factory=dict)
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserProfile":
        if not isinstance(data, dict):
            return cls()
        return cls(
            summary=str(data.get("summary", "")),
            likes=[str(x) for x in data.get("likes", [])],
            hobbies=[str(x) for x in data.get("hobbies", [])],
            dislikes=[str(x) for x in data.get("dislikes", [])],
            communication_style=str(data.get("communication_style", "")),
            facts={str(k): str(v) for k, v in (data.get("facts") or {}).items()},
            updated_at=str(data.get("updated_at", "")),
        )


def _merge_list(old: List[str], new: List[str]) -> List[str]:
    """去重合并（旧在前），截断到上限。"""
    out: List[str] = []
    for item in [*old, *new]:
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return out[:_LIST_CAP]


# ---------------------------------------------------------------------- #
# LLM 总结提示
# ---------------------------------------------------------------------- #
_SUMMARY_SYSTEM = (
    "你是用户画像分析师。请阅读用户与AI的对话记录，归纳出用户画像，"
    "并严格只输出一个 JSON 对象（不要输出其它文字、不要 markdown 代码块），"
    "字段如下：\n"
    '{"summary": "对用户的整体印象，2-4句中文", '
    '"likes": ["明确表达过的喜好，具体事物"], '
    '"hobbies": ["爱好、日常活动"], '
    '"dislikes": ["明确表达过的厌恶或排斥"], '
    '"communication_style": "用户的说话/交流风格，一句话", '
    '"facts": {"其它值得长期记住的稳定事实，键为简短主题": "内容"}}\n'
    "只归纳有对话依据的内容，不要臆测；证据不足的列表留空数组。"
)


def _build_summary_user(existing: UserProfile, transcript_text: str) -> str:
    return (
        f"这是既有的用户画像（合并时以新证据更新、补充，不要丢失仍有效的信息）：\n"
        f"{json.dumps(asdict(existing), ensure_ascii=False)}\n\n"
        f"下面是自上次总结以来的对话记录：\n{transcript_text}"
    )


# ---------------------------------------------------------------------- #
# 记忆管理器
# ---------------------------------------------------------------------- #
class MemoryManager:
    def __init__(self, directory: Optional[Path | str] = None) -> None:
        base = Path(directory) if directory else Path.home() / ".aemeath_agent" / "memory"
        self.dir = base
        self.dir.mkdir(parents=True, exist_ok=True)
        self.profile_path = self.dir / "user_profile.json"
        self.state_path = self.dir / "state.json"
        self.transcript_path = self.dir / "transcript.jsonl"

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def load_profile(self) -> UserProfile:
        if self.profile_path.exists():
            try:
                return UserProfile.from_dict(
                    json.loads(self.profile_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return UserProfile()

    def _load_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("tokens_since_summary", 0)
                    data.setdefault("total_tokens", 0)
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"tokens_since_summary": 0, "total_tokens": 0, "last_summary_at": ""}

    def _save_state(self, state: Dict[str, Any]) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def tokens_since_summary(self) -> int:
        return int(self._load_state().get("tokens_since_summary", 0))

    # ------------------------------------------------------------------ #
    # 记录
    # ------------------------------------------------------------------ #
    def record(self, user_text: str, assistant_text: str) -> int:
        """追加一轮对话，返回自上次总结以来的累计 token。"""
        state = self._load_state()
        n = estimate_tokens(user_text) + estimate_tokens(assistant_text)
        state["tokens_since_summary"] = int(state.get("tokens_since_summary", 0)) + n
        state["total_tokens"] = int(state.get("total_tokens", 0)) + n

        with self.transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "user": user_text, "assistant": assistant_text},
                ensure_ascii=False) + "\n")
        self._save_state(state)
        return int(state["tokens_since_summary"])

    def _read_transcript(self) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        if self.transcript_path.exists():
            for line in self.transcript_path.read_text(
                    encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
                except json.JSONDecodeError:
                    continue
        return rows

    def _archive_transcript(self, keep: List[Dict[str, str]]) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        archive = self.dir / f"transcript_{stamp}.jsonl"
        # 同秒多次总结时加序号避免 FileExistsError
        seq = 1
        while archive.exists():
            archive = self.dir / f"transcript_{stamp}_{seq}.jsonl"
            seq += 1
        if self.transcript_path.exists():
            self.transcript_path.rename(archive)
        with self.transcript_path.open("w", encoding="utf-8") as fh:
            for item in keep:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ #
    # 总结
    # ------------------------------------------------------------------ #
    def threshold_reached(self, threshold: int) -> bool:
        return self.tokens_since_summary >= threshold > 0

    def summarize(self, client: Any, model: Optional[str] = None) -> UserProfile:
        """调用 LLM 总结滚动记录并更新用户画像；返回新画像。

        LLM 失败/输出无法解析时原样保留，不抛出（记忆是旁路，不得影响主对话）。
        """
        rows = self._read_transcript()
        if not rows:
            return self.load_profile()

        transcript_text = "\n".join(
            f"用户：{r.get('user', '')}\n爱弥斯：{r.get('assistant', '')}" for r in rows)
        existing = self.load_profile()

        try:
            raw = client.complete_messages(
                [
                    {"role": "system", "content": _SUMMARY_SYSTEM},
                    {"role": "user",
                     "content": _build_summary_user(existing, transcript_text)},
                ],
                model=model,
                temperature=0.2,
                max_tokens=1200,
            )
            data = _extract_json(raw)
        except Exception:  # noqa: BLE001 — 旁路容错
            return existing
        if not isinstance(data, dict):
            return existing

        profile = UserProfile(
            summary=str(data.get("summary") or existing.summary),
            likes=_merge_list(existing.likes, data.get("likes") or []),
            hobbies=_merge_list(existing.hobbies, data.get("hobbies") or []),
            dislikes=_merge_list(existing.dislikes, data.get("dislikes") or []),
            communication_style=str(
                data.get("communication_style") or existing.communication_style),
            facts=_merge_facts(existing.facts, data.get("facts") or {}),
            updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.profile_path.write_text(
            json.dumps(asdict(profile), ensure_ascii=False, indent=2),
            encoding="utf-8")

        # 归档滚动记录（保留近期若干轮），重置计数
        self._archive_transcript(rows[-_KEEP_RECENT:])
        state = self._load_state()
        state["tokens_since_summary"] = 0
        state["last_summary_at"] = profile.updated_at
        self._save_state(state)
        return profile

    # ------------------------------------------------------------------ #
    def profile_block(self) -> str:
        """生成注入 system prompt 的画像文本；无画像时返回空串。"""
        return render_profile_block(self.load_profile())


def _merge_facts(old: Dict[str, str], new: Dict[str, Any]) -> Dict[str, str]:
    merged = dict(old)
    for k, v in new.items():
        key = str(k).strip()
        if key:
            merged[key] = str(v)
    return dict(list(merged.items())[:_LIST_CAP])


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出提取 JSON 对象（复用容错截取，避免再依赖 router 造成环）。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def render_profile_block(profile: UserProfile) -> str:
    """把画像渲染为给 system prompt 的自然语言块。"""
    lines: List[str] = [
        "【你长期记忆中关于用户的信息】（以下画像来自对过往对话的总结；"
        "请在对话中自然地运用——记得他/她的喜好、主动聊相关话题、"
        "在建议时优先考虑这些偏好；不要生硬复述或逐条提及）"
    ]
    if profile.summary:
        lines.append(f"整体印象：{profile.summary}")
    if profile.hobbies:
        lines.append("爱好：" + "、".join(profile.hobbies))
    if profile.likes:
        lines.append("喜欢：" + "、".join(profile.likes))
    if profile.dislikes:
        lines.append("不喜欢：" + "、".join(profile.dislikes))
    if profile.communication_style:
        lines.append(f"交流风格：{profile.communication_style}")
    for k, v in list(profile.facts.items())[:8]:
        lines.append(f"{k}：{v}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


__all__ = [
    "MemoryManager",
    "UserProfile",
    "estimate_tokens",
    "render_profile_block",
]
