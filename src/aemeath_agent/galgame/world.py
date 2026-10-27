"""心动模式 · 现实世界上下文（时间 / 天气 / 地点）。

给大模型提供「此刻玩家所处的真实世界」：本地时间与时段、季节、星期，
以及用户所在地的实时天气。三项均可与对话、随机事件、心链事件联动。

地点解析顺序：环境变量 ``AEMEATH_AGENT_CITY`` → 本地缓存 → IP 定位（免 key，
多源依次降级：ipwho.is / api.ip.sb / ipinfo.io）。
天气来源 Open-Meteo（免 key）。任一环节失败都降级为「仅时间」，不阻断对话。
结果缓存 :data:`CACHE_TTL_SECONDS` 秒，落盘 ``galgame/world_cache.json``。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from .state import save_dir

# 缓存有效期（秒）：天气变化不快，避免每次对话都打网络
CACHE_TTL_SECONDS = 20 * 60
# 外部接口超时（秒）：宁可降级也不要拖慢对话
_HTTP_TIMEOUT = 4.0

# 免 key 的 IP 定位源（按顺序降级）：某些源对非浏览器 UA 或国内网络不可用
_IP_APIS: tuple[tuple[str, str], ...] = (
    ("ipwho.is", "https://ipwho.is/"),
    ("api.ip.sb", "https://api.ip.sb/geoip"),
    ("ipinfo.io", "https://ipinfo.io/json"),
)
_WEATHER_API = "https://api.open-meteo.com/v1/forecast"
# 部分接口会拒绝默认 UA
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Aemeath/1.0"}

# WMO 天气代码 → 中文描述 + 归类
_WMO: Dict[int, tuple[str, str]] = {
    0: ("晴", "clear"),
    1: ("晴间多云", "clear"),
    2: ("多云", "cloudy"),
    3: ("阴", "cloudy"),
    45: ("有雾", "fog"),
    48: ("雾凇", "fog"),
    51: ("毛毛雨", "rain"),
    53: ("小雨", "rain"),
    55: ("细雨", "rain"),
    56: ("冻雨", "rain"),
    57: ("冻雨", "rain"),
    61: ("小雨", "rain"),
    63: ("中雨", "rain"),
    65: ("大雨", "rain"),
    66: ("冻雨", "rain"),
    67: ("强冻雨", "rain"),
    71: ("小雪", "snow"),
    73: ("中雪", "snow"),
    75: ("大雪", "snow"),
    77: ("雪粒", "snow"),
    80: ("阵雨", "rain"),
    81: ("强阵雨", "rain"),
    82: ("暴雨", "rain"),
    85: ("阵雪", "snow"),
    86: ("强阵雪", "snow"),
    95: ("雷阵雨", "storm"),
    96: ("雷暴伴冰雹", "storm"),
    99: ("强雷暴冰雹", "storm"),
}

# 时段划分：(起始小时, 时段名, 时段标签)
_PERIODS = [
    (5, "清晨", "morning"),
    (8, "上午", "morning"),
    (11, "正午", "noon"),
    (13, "午后", "afternoon"),
    (17, "傍晚", "evening"),
    (19, "夜晚", "night"),
    (23, "深夜", "latenight"),
]

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _period_of(hour: int) -> tuple[str, str]:
    label, tag = "深夜", "latenight"
    for start, name, t in _PERIODS:
        if hour >= start:
            label, tag = name, t
    return label, tag


def _season_of(month: int) -> str:
    if month in (3, 4, 5):
        return "春"
    if month in (6, 7, 8):
        return "夏"
    if month in (9, 10, 11):
        return "秋"
    return "冬"


def weather_label(code: Any) -> tuple[str, str]:
    """WMO 代码 → （中文描述, 归类）；未知代码归类为 cloudy。"""
    try:
        return _WMO.get(int(code), ("天气不明", "cloudy"))
    except (TypeError, ValueError):
        return ("天气不明", "cloudy")


@dataclass
class WorldContext:
    """一次对话时的现实世界快照。"""

    city: str = ""                     # 城市（可能为空）
    region: str = ""                   # 省 / 州
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    local_time: str = ""               # "2026-09-25 21:30"
    hour: int = 0
    weekday: str = ""
    time_period: str = ""              # 中文时段名
    time_tag: str = ""                 # morning / noon / ... / latenight
    season: str = ""
    date_text: str = ""                # "9 月 25 日"
    weather: str = ""                  # 中文天气描述（空=未知）
    weather_tag: str = ""              # clear / cloudy / rain / snow / fog / storm
    temperature: str = ""              # "23°C"
    is_day: Optional[bool] = None
    source: str = "local"              # local / ipapi.co+open-meteo
    fetched_at: str = ""

    # ------------------------------------------------------------------ #
    @property
    def has_weather(self) -> bool:
        return bool(self.weather)

    def place_text(self) -> str:
        """人类可读的地点描述（省 · 市），无数据时返回空串。"""
        parts = [p for p in (self.region, self.city) if p]
        if len(parts) == 2 and parts[0] == parts[1]:
            parts = parts[:1]
        return " · ".join(parts)

    def to_prompt(self) -> str:
        """渲染给大模型的现实世界块。"""
        lines = [
            f"- 此刻时间：{self.local_time}（{self.weekday}，{self.season}季的"
            f"{self.time_period}）",
        ]
        place = self.place_text()
        if place:
            lines.append(f"- 玩家所在地：{place}")
        if self.has_weather:
            temp = f"，气温 {self.temperature}" if self.temperature else ""
            lines.append(f"- 当地天气：{self.weather}{temp}")
        else:
            lines.append("- 当地天气：未知（不要凭空编造天气）")
        return (
            "【此刻的现实世界】\n" + "\n".join(lines) + "\n"
            "请让对话、正在发生的事件自然地呼应上面的时间与天气："
            "例如深夜要压低声音、下雨天会提到雨声与伞、寒冷时关心对方穿得够不够。"
            "但**不要**每轮都强行复述时间天气，只在合适的时候自然带入。"
        )

    def to_public(self) -> Dict[str, Any]:
        """给前端的公开视图（HUD 显示用）。"""
        data = asdict(self)
        data["place_text"] = self.place_text()
        return data


# --------------------------------------------------------------------------- #
# 采集
# --------------------------------------------------------------------------- #
def _cache_path() -> Path:
    return save_dir() / "world_cache.json"


def _now() -> datetime:
    return datetime.now()


def time_only() -> WorldContext:
    """离线降级：只用本机时间，不联网。"""
    now = _now()
    period, tag = _period_of(now.hour)
    return WorldContext(
        local_time=now.strftime("%Y-%m-%d %H:%M"),
        hour=now.hour,
        weekday=_WEEKDAYS[now.weekday()],
        time_period=period,
        time_tag=tag,
        season=_season_of(now.month),
        date_text=f"{now.month} 月 {now.day} 日",
        source="local",
        fetched_at=now.isoformat(timespec="seconds"),
    )


def _read_cache() -> Optional[WorldContext]:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(str(data.get("fetched_at", "")))
        if (_now() - fetched).total_seconds() > CACHE_TTL_SECONDS:
            return None
        return WorldContext(**{k: v for k, v in data.items()
                               if k in WorldContext.__dataclass_fields__})
    except (ValueError, TypeError, OSError):
        return None


def _write_cache(ctx: WorldContext) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(ctx), ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        pass  # 缓存写失败不影响对话


def _clean_region(name: str) -> str:
    """清理英文后缀，让地名更适合展示（Sichuan Sheng → Sichuan）。"""
    for suffix in (" Sheng", " Shi", " Province", " Autonomous Region"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def _normalize_location(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把不同源的响应统一成 {city, region, latitude, longitude}。"""
    lat, lon = raw.get("latitude"), raw.get("longitude")
    if lat is None or lon is None:
        loc = str(raw.get("loc") or "")       # ipinfo.io 的 "30.66,104.06"
        if "," in loc:
            left, _, right = loc.partition(",")
            lat, lon = left.strip(), right.strip()
    return {
        "city": str(raw.get("city") or "").strip(),
        "region": _clean_region(str(raw.get("region") or "")),
        "latitude": lat,
        "longitude": lon,
    }


def _fetch_location() -> Dict[str, Any]:
    """依次尝试各免 key 定位源；全部失败返回空字典。"""
    manual = os.environ.get("AEMEATH_AGENT_CITY", "").strip()
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_HEADERS) as client:
        for _name, url in _IP_APIS:
            try:
                resp = client.get(url)
                resp.raise_for_status()
                data = _normalize_location(resp.json())
            except Exception:  # noqa: BLE001 — 换下一个源
                continue
            if manual:
                data["city"] = manual
            if data["city"] or data["region"]:
                return data
    return {"city": manual} if manual else {}


def _fetch_weather(lat: float, lon: float) -> Dict[str, Any]:
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_HEADERS) as client:
        resp = client.get(_WEATHER_API, params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code,is_day",
            "timezone": "auto",
        })
        resp.raise_for_status()
        return resp.json().get("current", {}) or {}


def get_world(*, force: bool = False) -> WorldContext:
    """获取当前现实世界上下文（带缓存与离线降级，永不抛异常）。"""
    if not force:
        cached = _read_cache()
        if cached is not None:
            # 缓存里的时间信息会过期，用实时时间覆盖
            fresh = time_only()
            cached.local_time = fresh.local_time
            cached.hour = fresh.hour
            cached.weekday = fresh.weekday
            cached.time_period = fresh.time_period
            cached.time_tag = fresh.time_tag
            cached.date_text = fresh.date_text
            # 季节以缓存时的地点为准（南半球可能不同），故不覆盖
            return cached

    ctx = time_only()
    try:
        loc = _fetch_location()
        ctx.city = str(loc.get("city") or "")
        ctx.region = str(loc.get("region") or "")
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if lat is not None and lon is not None:
            ctx.latitude, ctx.longitude = float(lat), float(lon)
        ctx.source = "geo+open-meteo" if (ctx.city or ctx.region) else "local"
        if ctx.latitude is not None and ctx.longitude is not None:
            current = _fetch_weather(ctx.latitude, ctx.longitude)
            label, tag = weather_label(current.get("weather_code"))
            ctx.weather, ctx.weather_tag = label, tag
            temp = current.get("temperature_2m")
            if temp is not None:
                ctx.temperature = f"{round(float(temp))}°C"
            is_day = current.get("is_day")
            ctx.is_day = bool(is_day) if is_day is not None else None
        _write_cache(ctx)
    except Exception:  # noqa: BLE001 — 联网失败一律降级为仅时间
        ctx.source = "local"
    return ctx


def event_world_weight(ev_weather: tuple[str, ...], ev_time: tuple[str, ...],
                       world: Optional[WorldContext]) -> float:
    """事件与当前时间/天气的契合度权重（1.0 为中性）。"""
    if world is None:
        return 1.0
    weight = 1.0
    if ev_time:
        weight *= 1.8 if world.time_tag in ev_time else 0.55
    if ev_weather:
        if not world.has_weather:
            weight *= 1.0            # 天气未知时不做区分
        else:
            weight *= 1.8 if world.weather_tag in ev_weather else 0.55
    return weight