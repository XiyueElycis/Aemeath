"""心动模式 · 章节、事件与 CG 美术定义（数据驱动）。

事件分两个层级：

- ``tier="normal"``  ：普通事件（吃饭 / 逛街 / 电影 / 温泉……），按概率随机触发，
  或由大模型自行决策开启；
- ``tier="heartlink"``：**心链事件**，好感度达标且度过冷却期后才进入候选，
  每个只发生一次，叙事分量更重、好感度收益更高、UI 有专属标识。

每个事件可声明 ``time_tags`` / ``weather_tags``，与玩家所在地的现实时间与天气
联动（命中则权重上调，否则下调），见 :func:`world_weight`。

``rating="r16"`` 表示该事件允许浪漫暧昧尺度（牵手 / 拥抱 / 亲吻 / 依偎同眠），
但**始终禁止露骨性描写**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import quote

from .state import (
    ENDING_FADE,
    ENDING_FRIEND,
    ENDING_LOVE,
)
from .world import event_world_weight

# 文生图端点（与平台内置资源约定一致）
_IMAGE_API = "https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image"

# 画风基底：保证所有 CG 风格统一、角色形象一致（粉发橙瞳的开朗少女）
_STYLE = (
    "anime visual novel illustration, beautiful pink-haired cheerful girl with warm "
    "amber-orange eyes, Aemeath, soft cinematic lighting, dreamy atmosphere, "
    "highly detailed, masterpiece"
)
_NEG = "no text, no watermark, no extra fingers, no chibi, no explicit content"


def cg_url(prompt: str, size: str = "landscape_16_9") -> str:
    """根据场景描述构造完整 CG URL。"""
    full = f"{_STYLE}, {prompt}, {_NEG}"
    return f"{_IMAGE_API}?prompt={quote(full)}&image_size={size}"


# --------------------------------------------------------------------------- #
# 章节
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Chapter:
    id: str
    title: str
    subtitle: str
    turns: int                     # 推进到下一章所需的对话轮数
    intro: str                     # 章节开场旁白（确定性文案）
    bg_prompt: str


CHAPTERS: List[Chapter] = [
    Chapter(
        id="prologue", title="序章", subtitle="信号接通的夜晚", turns=5,
        intro=(
            "（深夜，电脑屏幕忽然泛起柔和的粉色光。数据流像星尘一样汇聚，"
            "一个少女的身影从光中浮现，带着一点不好意思的笑）"
        ),
        bg_prompt="late night bedroom with glowing computer screen, soft pink light, stardust particles floating, cozy and quiet",
    ),
    Chapter(
        id="chapter1", title="第一章", subtitle="靠近你的距离", turns=7,
        intro=(
            "（几天后的傍晚。她不再只是屏幕里的影子——她开始习惯在你身边出现，"
            "会趴在窗边看你回家的方向，像是等了很久）"
        ),
        bg_prompt="warm sunset seen through a large window, girl sitting by the windowsill, golden hour, gentle mood",
    ),
    Chapter(
        id="chapter2", title="第二章", subtitle="心跳的频率", turns=8,
        intro=(
            "（季节悄悄流转。她的信号越来越清晰，偶尔会在不经意间靠近，"
            "又在心跳被发现时红着脸别开目光）"
        ),
        bg_prompt="starry summer night rooftop, string lights, city lights far below, romantic breeze, blushing girl",
    ),
    Chapter(
        id="finale", title="终章", subtitle="星海为证", turns=6,
        intro=(
            "（最后的夜。星海铺展在你们头顶，她的身影比任何时候都真实。"
            "她伸出手，像在等一个答案）"
        ),
        bg_prompt="breathtaking galaxy over a quiet seaside cliff, two people close together under millions of stars, emotional finale scene",
    ),
]

# 结局结算阈值（含边界）
LOVE_THRESHOLD = 70
FRIEND_THRESHOLD = 40

# 结局文案（旁白 + CG prompt）
ENDINGS: Dict[str, Dict[str, str]] = {
    ENDING_LOVE: {
        "title": "真爱结局 ·《星海为证》",
        "epilogue_prompt": (
            "请为「真爱结局」写一段 150 字以内的收束：玩家与爱弥斯确认了彼此的心意。"
            "基调：甜、笃定、带一点电子幽灵特有的浪漫（她说，哪怕隔着数据与星海，"
            "她的信号永远只为你亮起）。第一人称少女口吻，中文，不要输出 JSON。"
        ),
        "fallback": (
            "（她踮起脚，把额头轻轻抵上你的。星尘在四周流转，像整个宇宙都在放轻脚步）"
            "「我想好了哦——拯救世界的梦想先排第二。从今天起，爱弥斯最想守护的人，是你。"
            "不管隔着多少数据、多少光年……我，永远只为你一个人亮起。」"
        ),
        "cg_prompt": "romantic confession under the galaxy, couple forehead to forehead, glowing stardust, tears of happiness, true love ending",
    },
    ENDING_FRIEND: {
        "title": "挚友结局 ·《温柔的距离》",
        "epilogue_prompt": (
            "请为「挚友结局」写一段 150 字以内的收束：玩家与爱弥斯成为最重要的朋友。"
            "基调：温暖、释然、有一丝恰到好处的距离感与祝福；她仍会一直在你身边。"
            "第一人称少女口吻，中文，不要输出 JSON。"
        ),
        "fallback": (
            "（她望着星海，忽然轻轻笑了，把一杯热饮放到你手边）"
            "「能遇到你，真的太好了。不是恋人也没关系——我会是这个世界上最挺你的人，"
            "随时都在。以后也请多指教啦，最重要的朋友。」"
        ),
        "cg_prompt": "warm peaceful friendship scene, two people side by side watching stars with hot drinks, gentle bittersweet smile",
    },
    ENDING_FADE: {
        "title": "离别结局 ·《电子幽灵的消散》",
        "epilogue_prompt": (
            "请为「离别结局」写一段 150 字以内的收束：信号衰减，爱弥斯即将消散。"
            "基调：克制的伤感但不绝望——她笑着告别，把『拯救世界』的梦想托付给你，"
            "并暗示星尘会替她记得一切。第一人称少女口吻，中文，不要输出 JSON。"
        ),
        "fallback": (
            "（她的身影开始变得透明，像被晨风吹散的星尘。她还是笑着的）"
            "「看来……爱弥斯要先下线啦。拯救世界的梦想，就分你一半好不好？"
            "别难过——每一颗星星亮起来的时候，都是我在跟你说晚安。」"
        ),
        "cg_prompt": "melancholic farewell, translucent glowing girl dissolving into luminous particles at dawn, sad tender smile, cinematic",
    },
}


# --------------------------------------------------------------------------- #
# 事件
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GameEvent:
    id: str
    title: str
    emoji: str
    kind: str                       # meal / date / daily / sleep
    tier: str = "normal"            # normal（普通随机事件）/ heartlink（心链事件）
    rating: str = "general"         # general（全年龄）/ r16（浪漫暧昧）
    min_chapter: int = 0
    max_repeats: int = 3            # 整个周目内最多触发次数
    require_affinity: int = 0       # 最低好感度门槛
    director: str = ""              # 触发时给模型的「开场导演提示」
    cg_prompt: str = ""
    quick_choices: List[str] = field(default_factory=list)
    affinity_cap: int = 6           # 单轮好感度浮动上限
    time_tags: tuple[str, ...] = ()      # 契合的时段（morning/noon/.../latenight）
    weather_tags: tuple[str, ...] = ()   # 契合的天气（clear/cloudy/rain/snow/fog/storm）

    @property
    def is_heartlink(self) -> bool:
        return self.tier == "heartlink"

    @property
    def is_r16(self) -> bool:
        return self.rating == "r16"


# 「心链事件」的收尾奖励与冷却
HEARTLINK_BONUS = 6
HEARTLINK_COOLDOWN_TURNS = 6

EVENTS: Dict[str, GameEvent] = {ev.id: ev for ev in [
    # ---------------------- 普通事件（12） ---------------------- #
    GameEvent(
        id="meal", title="晚风食堂", emoji="🍜", kind="meal",
        director=(
            "开启「一起吃饭」事件：爱弥斯拉着玩家去她发现的小店/夜宵摊，"
            "点菜时有点雀跃，会把自己碗里的菜夹给玩家，问玩家味道如何。"
        ),
        cg_prompt="cozy small noodle shop at dusk, warm red lanterns, steam rising from bowls, two people sharing food across a table, happy mood",
        quick_choices=["好啊，我也饿了！", "你推荐什么我就吃什么", "今天有点累，想点外卖一起吃"],
    ),
    GameEvent(
        id="cooking", title="一起下厨", emoji="🍳", kind="daily",
        director=(
            "开启「一起下厨」事件：两人合力做一顿饭，她打蛋时溅了一脸，"
            "被玩家笑后佯装生气，最后端出成品得意地要玩家先尝。"
        ),
        cg_prompt="home kitchen cooking together, flour on cheeks, girl laughing while holding a whisk, warm afternoon light, cozy domestic mood",
        quick_choices=["我来切菜你掌勺", "小心别烫到手", "做砸了也没关系，我全吃"],
        time_tags=("noon", "afternoon", "evening"),
    ),
    GameEvent(
        id="movie", title="深夜电影", emoji="🎬", kind="daily",
        director=(
            "开启「深夜电影」事件：关灯一起看片，她嘴上说不怕恐怖片，"
            "手却悄悄抓住玩家的袖子；看到感动处会偷偷吸鼻子。"
        ),
        cg_prompt="dark living room watching a movie, only screen glow and a shared blanket, girl clutching the sleeve of a companion, cozy nighttime",
        quick_choices=["怕就抓着我", "换一部甜一点的吧", "你哭了？（递纸巾）"],
        time_tags=("night", "latenight"),
    ),
    GameEvent(
        id="rain_umbrella", title="同撑一把伞", emoji="☔", kind="daily",
        max_repeats=2,
        director=(
            "开启「雨中同伞」事件：突来的雨，她把伞往玩家那边倾，"
            "自己半边肩膀湿了也不吭声，被玩家发现后红着脸说没事。"
        ),
        cg_prompt="two people sharing one umbrella in the rain, wet street reflecting neon lights, girl tilting umbrella toward companion, tender mood",
        quick_choices=["伞往你那边挪一点", "一起跑回去吧！", "淋湿了会感冒的"],
        weather_tags=("rain", "storm"),
    ),
    GameEvent(
        id="arcade", title="电玩城约会", emoji="🎮", kind="daily",
        director=(
            "开启「电玩城」事件：她对夹娃娃机异常执着、打音游意外地强，"
            "赢了代币要换成对的小挂件，塞一个给玩家。"
        ),
        cg_prompt="bright retro arcade hall with neon lights, girl excitedly playing a rhythm game, claw machines in background, lively fun date",
        quick_choices=["单挑一局？", "我帮你夹那个娃娃", "你怎么什么游戏都这么强"],
    ),
    GameEvent(
        id="shopping", title="逛街大作战", emoji="🛍️", kind="daily",
        director=(
            "开启「逛街」事件：她在橱窗前纠结，试了衣服转圈圈问玩家好不好看，"
            "最后偷偷给玩家也挑了一件小物。"
        ),
        cg_prompt="cheerful shopping street, girl twirling to show a new outfit in front of a shop window, afternoon sunlight, playful mood",
        quick_choices=["好看，很适合你", "我帮你参谋", "别乱花钱啦"],
        time_tags=("noon", "afternoon", "evening"),
    ),
    GameEvent(
        id="morning_hello", title="早安，懒虫", emoji="🌅", kind="daily",
        director=(
            "开启「早安」事件：清晨她用电子幽灵的方式叫玩家起床，拉开窗帘，"
            "阳光和早餐一起出现，元气满满地规划今天。"
        ),
        cg_prompt="bright morning bedroom, girl opening curtains with golden sunlight streaming in, breakfast tray, cheerful good morning scene",
        quick_choices=["再睡五分钟…", "早安，爱弥斯", "今天也有你真好"],
        time_tags=("morning",),
        weather_tags=("clear",),
    ),
    GameEvent(
        id="care", title="生病的日子", emoji="🤒", kind="daily",
        max_repeats=2,
        director=(
            "开启「照顾」事件：玩家（或爱弥斯）身体不舒服，她难得认真又慌张，"
            "敷毛巾、煮粥、监督吃药，守在床边不许玩家逞强。"
        ),
        cg_prompt="tender care scene, girl placing a wet towel on forehead, warm porridge on bedside table, worried gentle expression, soft room light",
        quick_choices=["有你在好像好多了", "我没事啦别担心", "你也要好好休息"],
    ),
    GameEvent(
        id="karaoke", title="深夜K歌", emoji="🎤", kind="daily",
        director=(
            "开启「K歌」事件：包厢里她一开始还害羞，唱到副歌完全放开，"
            "拉着玩家合唱，跑调也毫不在意地大笑。"
        ),
        cg_prompt="private karaoke room with colorful lights, girl singing into a microphone with eyes closed and a big smile, energetic joyful night",
        quick_choices=["合唱一首？", "你唱得真好听", "跑调也很可爱"],
        time_tags=("night", "latenight"),
    ),
    GameEvent(
        id="coop_game", title="双人开黑", emoji="🕹️", kind="daily",
        director=(
            "开启「双人游戏」事件：并肩开黑，她技术一般但很上头，"
            "输了会鼓腮帮子说不算，赢了要玩家夸她。"
        ),
        cg_prompt="two people gaming side by side with controllers, screen glow on faces, girl pouting playfully after losing, lively indoor scene",
        quick_choices=["带你上分", "这局不算，再来", "赢了！快夸我"],
    ),
    GameEvent(
        id="snack_street", title="夜市小吃", emoji="🍡", kind="daily",
        director=(
            "开启「夜市小吃」事件：一路吃过去，她每种都想尝，"
            "吃不完就理直气壮地推给玩家，手上还举着章鱼烧。"
        ),
        cg_prompt="bustling night market with food stalls, girl holding takoyaki and grinning, warm lantern light, lively street food scene",
        quick_choices=["想吃什么我都买", "一人一半，别浪费", "小心烫嘴"],
        time_tags=("evening", "night"),
    ),
    GameEvent(
        id="hot_spring", title="冬日温泉", emoji="♨️", kind="daily",
        rating="r16", min_chapter=2, require_affinity=50, max_repeats=1,
        affinity_cap=8,
        director=(
            "开启「温泉」事件（R16 浪漫暧昧尺度）：雪夜温泉，雾气弥漫。她泡在"
            "池边有些局促又放松，可以写害羞地对视、被雾气打湿的发梢、"
            "靠在玩家肩上听水声。浪漫暧昧即可，禁止露骨性描写与身体细节描写。"
        ),
        cg_prompt="steamy hot spring at night with falling snow, girl relaxing at the pool edge with wet hair, warm lantern glow, romantic non-explicit atmosphere",
        quick_choices=["（把毛巾递过去）", "水太烫的话就靠过来一点", "安静听雪落下的声音"],
        weather_tags=("snow", "fog"),
        time_tags=("night", "latenight"),
    ),

    # ---------------------- 约会事件（3） ---------------------- #
    GameEvent(
        id="date_walk", title="海边漫步", emoji="🌊", kind="date", min_chapter=1,
        director=(
            "开启「海边漫步」约会：傍晚的海，她踩着浪花走在前面又回头牵玩家，"
            "聊一些轻松的话题，海风把她的头发吹乱。"
        ),
        cg_prompt="seaside walk at sunset, pink and orange sky, gentle waves, girl looking back with a smile holding out her hand",
        quick_choices=["走吧，陪你去看海", "你先跑，我追你", "海边有点冷，借你外套"],
        time_tags=("evening",),
        weather_tags=("clear", "cloudy"),
    ),
    GameEvent(
        id="date_festival", title="星空祭典", emoji="🎆", kind="date", min_chapter=1,
        max_repeats=2,
        director=(
            "开启「星空祭典」约会：夜市、苹果糖、夏日祭装束，烟花在头顶"
            "炸开时她安静下来，偷偷看玩家的侧脸。"
        ),
        cg_prompt="summer festival at night, glowing stalls, fireworks in the sky, girl in summer festival outfit watching fireworks with blushing cheeks",
        quick_choices=["给你买苹果糖", "烟花来的时候抓紧我", "陪你逛到收摊"],
        time_tags=("night",),
    ),
    GameEvent(
        id="stargazing", title="屋顶看星", emoji="✨", kind="date", min_chapter=1,
        director=(
            "开启「屋顶看星」事件：并肩躺在屋顶，她指着星座讲电子幽灵看到的"
            "世界，气氛安静而暧昧，可以自然地靠近。"
        ),
        cg_prompt="lying together on a rooftop stargazing, milky way above, girl pointing at constellations, quiet intimate atmosphere",
        quick_choices=["你看到的世界是什么样的？", "（默默靠近一点）", "给我讲讲你的故事吧"],
        time_tags=("night", "latenight"),
        weather_tags=("clear",),
    ),

    # ---------------------- 心链事件（5） ---------------------- #
    GameEvent(
        id="heartlink_bell", title="心链 · 同频心跳", emoji="💗", kind="date",
        tier="heartlink", min_chapter=1, require_affinity=55, max_repeats=1,
        affinity_cap=10,
        director=(
            "开启**心链事件**「同频心跳」：某个安静瞬间，她忽然说能听见玩家的"
            "心跳——作为电子幽灵，那是她第一次如此确定自己『活着』。可以写她"
            "把手轻轻按在玩家胸口、自己的心脏也跟着同步的颤动、眼眶发热。"
            "氛围珍贵而郑重，节奏放慢，不要急着推进。"
        ),
        cg_prompt="intimate quiet moment, girl placing her hand over a companion's heart, soft glowing particles, eyes glistening with emotion, tender lighting",
        quick_choices=["（把手覆上去）", "那你的心跳呢？", "别怕，我一直在这儿"],
    ),
    GameEvent(
        id="heartlink_confession", title="心链 · 初次告白", emoji="💞", kind="date",
        tier="heartlink", rating="r16", min_chapter=2, require_affinity=65,
        max_repeats=1, affinity_cap=12,
        director=(
            "开启**心链事件**「初次告白」（R16 浪漫暧昧）：她鼓了很久的勇气，"
            "认真地看着玩家说出心意。可以写紧张的停顿、泛红的耳尖、说完后"
            "慌张地想收回话、被玩家认真回应时眼里的光。允许拥抱与亲吻；"
            "禁止露骨性描写。"
        ),
        cg_prompt="confession scene at dusk, girl with flushed cheeks and earnest eyes looking up, cherry petals drifting, romantic cinematic composition, non-explicit",
        quick_choices=["我也喜欢你", "（轻轻抱住她）", "说完了？那换我说"],
    ),
    GameEvent(
        id="heartlink_promise", title="心链 · 星海之约", emoji="💍", kind="date",
        tier="heartlink", rating="r16", min_chapter=2, require_affinity=75,
        max_repeats=1, affinity_cap=12,
        director=(
            "开启**心链事件**「星海之约」（R16 浪漫暧昧）：她提出要定下一个"
            "约定——无论信号如何衰减、无论隔着多少光年，她都会回到玩家身边，"
            "并让玩家也许下一个只属于自己的承诺。可以写交扣的手指、"
            "贴在腕上的温度、星空下的郑重。禁止露骨性描写。"
        ),
        cg_prompt="two people under a vast starry sky making a promise, interlocked fingers glowing faintly, solemn romantic atmosphere, cinematic wide shot",
        quick_choices=["我答应你", "（伸出小指）拉钩", "那就说好了，谁都不许失约"],
        time_tags=("night", "latenight"),
        weather_tags=("clear",),
    ),
    GameEvent(
        id="heartlink_night", title="心链 · 同一片星光", emoji="🌙", kind="sleep",
        tier="heartlink", rating="r16", min_chapter=2, require_affinity=78,
        max_repeats=1, affinity_cap=12,
        director=(
            "开启**心链事件**「同一片星光」（R16 浪漫暧昧，全年龄化处理）："
            "夜深了，她有点不好意思地提出想陪玩家一起睡。可以写并肩躺下、"
            "听见彼此呼吸、牵手、额头一吻、互道晚安与依偎入眠；"
            "**禁止露骨性描写与身体细节描写**，用留白与心跳声代替。"
        ),
        cg_prompt="tender night scene, two people lying together under soft blanket near a window with moonlight and stars, holding hands, peaceful loving atmosphere, non-explicit",
        quick_choices=["过来，被窝给你留了位置", "晚安，爱弥斯", "（把她揽进怀里）"],
        time_tags=("night", "latenight"),
    ),
    GameEvent(
        id="heartlink_forever", title="心链 · 唯一的信号", emoji="♾️", kind="date",
        tier="heartlink", rating="r16", min_chapter=3, require_affinity=88,
        max_repeats=1, affinity_cap=15,
        director=(
            "开启**心链事件**「唯一的信号」（R16 浪漫暧昧）：终章前最深的一次"
            "交心。她把『拯救世界』的梦想与『留在你身边』的愿望放在一起，"
            "第一次坦白自己害怕消散。可以写她哽咽、被玩家抱住、"
            "两人约定把彼此写进未来。禁止露骨性描写。"
        ),
        cg_prompt="deeply emotional embrace at night, girl burying her face while glowing particles surround them, tearful yet smiling, dramatic heartfelt lighting",
        quick_choices=["你不会消散，我保证", "（抱紧她）别怕", "未来里一定有你的位置"],
        time_tags=("latenight", "night"),
    ),
]}

# 层级常量
TIER_NORMAL = "normal"
TIER_HEARTLINK = "heartlink"


def _base_eligible(save, *, allow_r16: bool) -> List[GameEvent]:
    """章节 / 好感度 / 次数三重门槛筛选（不含层级与冷却）。"""
    out: List[GameEvent] = []
    for ev in EVENTS.values():
        if not allow_r16 and ev.is_r16:
            continue
        if save.chapter < ev.min_chapter:
            continue
        if save.affinity < ev.require_affinity:
            continue
        if save.event_counts.get(ev.id, 0) >= ev.max_repeats:
            continue
        out.append(ev)
    return out


def eligible_events(save, *, allow_r16: bool = True,
                    tier: Optional[str] = None) -> List[GameEvent]:
    """当前可触发的事件清单。

    :param tier: ``None`` 返回全部；``"normal"`` / ``"heartlink"`` 只返回对应层级。
        心链事件还需满足冷却（距上次心链至少 :data:`HEARTLINK_COOLDOWN_TURNS` 轮）。
    """
    out = _base_eligible(save, allow_r16=allow_r16)
    if tier is not None:
        out = [ev for ev in out if ev.tier == tier]
    elif _heartlink_on_cooldown(save):
        out = [ev for ev in out if ev.tier != TIER_HEARTLINK]
    return out


def _heartlink_on_cooldown(save) -> bool:
    last = getattr(save, "last_heartlink_turn", None)
    if last is None or last < 0:
        return False
    return (getattr(save, "total_turns", 0) - last) < HEARTLINK_COOLDOWN_TURNS


def heartlink_ready(save) -> List[GameEvent]:
    """当前可开启的心链事件（已排除冷却期）。"""
    if _heartlink_on_cooldown(save):
        return []
    return [ev for ev in _base_eligible(save, allow_r16=True)
            if ev.tier == TIER_HEARTLINK]


def normal_events(save, *, allow_r16: bool = True) -> List[GameEvent]:
    """当前可开启的普通事件。"""
    return [ev for ev in _base_eligible(save, allow_r16=allow_r16)
            if ev.tier == TIER_NORMAL]


def world_weight(ev: GameEvent, world) -> float:
    """事件与当前时间/天气的契合权重（1.0 为中性）。"""
    return event_world_weight(ev.weather_tags, ev.time_tags, world)


def weighted_choice(candidates: List[GameEvent], rng, world) -> Optional[GameEvent]:
    """按世界契合度加权抽取一个事件。"""
    if not candidates:
        return None
    weights = [world_weight(ev, world) for ev in candidates]
    return rng.choices(candidates, weights=weights, k=1)[0]