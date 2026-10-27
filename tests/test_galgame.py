"""心动模式引擎单元测试（FakeLLM + 离线世界，不触网）。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from aemeath_agent.galgame import GalgameEngine
from aemeath_agent.galgame.engine import (
    HISTORY_LIMIT,
    MEMORY_CHAR_LIMIT,
    MEMORY_INTERVAL,
    MEMORY_KEEP,
    MEMORY_MAX_ITEMS,
    MEMORY_SUMMARY_TOKENS,
    PENDING_NOTE_MAX,
    PROACTIVE_DAILY_CAP,
    SALIENCE_PROMISE,
    _memory_mid,
    _norm_memory,
    _queue_note,
    _trim_memories,
)
from aemeath_agent.galgame.events import (
    CHAPTERS,
    ENDINGS,
    EVENTS,
    HEARTLINK_BONUS,
    TIER_HEARTLINK,
    TIER_NORMAL,
    heartlink_ready,
    normal_events,
    world_weight,
)
from aemeath_agent.galgame.memory import GalgameMemory
from aemeath_agent.galgame.retrieval import (
    EmbeddingRetriever,
    HashingRetriever,
    Retriever,
    build_retriever,
    tokenize,
)
from aemeath_agent.galgame.state import (
    ENDING_FADE,
    ENDING_FRIEND,
    ENDING_LOVE,
    MEMORY_KIND_EVENT,
    MEMORY_KIND_PLOT,
    MEMORY_KIND_PROMISE,
    ARCHIVE_KEEP,
    HISTORY_MAX_ITEMS,
    ActiveEvent,
    GalgameSave,
    archive_current,
    archive_dir,
    iter_archives,
    load_save,
    persist,
    save_path,
)
from aemeath_agent.galgame.world import WorldContext, time_only
from aemeath_agent.settings import Settings


class FakeLLM:
    """按脚本队列返回文本。"""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = 0

    def complete_messages(self, messages, model=None, temperature=None,
                          max_tokens=None):
        self.calls += 1
        self.last_messages = messages
        if self.scripts:
            return self.scripts.pop(0)
        return json.dumps({"reply": "（静默）", "narration": "",
                           "affinity_delta": 0, "event": None})


class TestableEngine(GalgameEngine):
    __test__ = False  # 告诉 pytest 这不是测试类

    def __init__(self, fake, settings=None, rng=None, memory=None,
                 retriever=None):
        super().__init__(settings=settings, rng=rng, memory=memory,
                         retriever=retriever)
        self._fake = fake

    def _client(self):
        return self._fake, "fake-model"


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    """隔离存档目录，避免污染真实进度。"""
    monkeypatch.setenv("AEMEATH_AGENT_DATA_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def offline_world(monkeypatch):
    """所有测试都用离线世界（仅本机时间），绝不联网。"""
    from aemeath_agent.galgame import engine as engine_mod

    monkeypatch.setattr(engine_mod, "get_world",
                        lambda force=False: time_only())


def run(coro):
    return asyncio.run(coro)


def _ok(reply="好哦", delta=0, event=None, narration=""):
    return json.dumps({"reply": reply, "narration": narration,
                       "affinity_delta": delta, "event": event})


def make_engine(fake, **kwargs):
    """构造引擎并禁用随机事件（默认不掷骰）。"""
    eng = TestableEngine(fake, **kwargs)
    eng.rng.random = lambda: 1.0
    return eng


# --------------------------------------------------------------------------- #
# 存档与基础流程
# --------------------------------------------------------------------------- #
def test_start_creates_save(data_home):
    eng = TestableEngine(FakeLLM([]))
    data = eng.start()
    assert data["save"]["started"] is True
    assert data["save"]["chapter"] == 0
    assert data["opening"] is True
    assert eng.start()["save"]["started"] is True


def test_reset_restarts(data_home):
    eng = TestableEngine(FakeLLM([]))
    eng.start()
    run(eng.chat("你好"))
    fresh = eng.reset()
    assert fresh["save"]["total_turns"] == 0
    assert fresh["save"]["ending"] is None
    assert fresh["save"]["last_heartlink_turn"] == -1


# --------------------------------------------------------------------------- #
# 好感度新逻辑
# --------------------------------------------------------------------------- #
def test_plain_chat_affinity(data_home):
    eng = make_engine(FakeLLM([_ok("今天过得怎么样？", delta=3)]))
    eng.start()
    out = run(eng.chat("今天想你了"))
    assert out["reply"] == "今天过得怎么样？"
    assert out["affinity_delta"] == 3
    assert out["save"]["affinity"] == 23


def test_growth_cap_diminishes_with_affinity():
    """越亲近涨得越慢，但保底 +2。"""
    from aemeath_agent.galgame.engine import growth_cap
    assert growth_cap(0, 5) == 5
    assert growth_cap(20, 5) == 4
    assert growth_cap(60, 5) == 3
    assert growth_cap(100, 5) == 2
    assert growth_cap(100, 2) == 2          # 保底


def test_affinity_clamped_to_100(data_home):
    eng = make_engine(FakeLLM([_ok(delta=999), _ok(delta=999)]))
    eng.start()
    out = run(eng.chat("爱你"))
    # 好感 20 时上涨上限被边际递减压到 4
    assert out["affinity_delta"] == 4
    assert out["save"]["affinity"] == 24

    save = GalgameSave(**{k: v for k, v in out["save"].items()
                          if k in GalgameSave.__dataclass_fields__})
    save.affinity = 99
    persist(save)
    out2 = run(eng.chat("超级爱你"))
    assert out2["save"]["affinity"] == 100


def test_negative_delta_not_reduced(data_home):
    """冷淡/冒犯照常扣分，不受边际递减影响。"""
    eng = make_engine(FakeLLM([_ok(delta=-4)]))
    eng.start()
    out = run(eng.chat("别烦我"))
    assert out["affinity_delta"] == -4
    assert out["save"]["affinity"] == 16


def test_affinity_notes_explain_adjustment(data_home):
    eng = make_engine(FakeLLM([_ok(delta=5)]))
    eng.start()
    out = run(eng.chat("最喜欢你了"))
    assert out["affinity_notes"], "调整说明不应为空"
    assert any("边际递减" in n for n in out["affinity_notes"])


# --------------------------------------------------------------------------- #
# 事件库
# --------------------------------------------------------------------------- #
def test_event_count_is_20():
    assert len(EVENTS) == 20


def test_event_tiers_and_r16_present():
    heart = [e for e in EVENTS.values() if e.tier == TIER_HEARTLINK]
    normal = [e for e in EVENTS.values() if e.tier == TIER_NORMAL]
    r16 = [e for e in EVENTS.values() if e.is_r16]
    assert len(heart) == 5
    assert len(normal) == 15
    assert len(r16) >= 3, "应存在部分 R16 尺度事件"
    # 心链事件每个周目只发生一次
    assert all(e.max_repeats == 1 for e in heart)
    # 心链门槛必须高于普通事件
    assert all(e.require_affinity >= 55 for e in heart)


def test_every_event_has_director_and_choices():
    for ev in EVENTS.values():
        assert ev.director.strip(), f"{ev.id} 缺少导演提示"
        assert ev.quick_choices, f"{ev.id} 缺少快捷选项"
        assert ev.cg_prompt.strip(), f"{ev.id} 缺少 CG 描述"


def test_random_event_forced_when_dice_hits(data_home):
    eng = TestableEngine(FakeLLM([_ok("走吧，去吃！")]), rng=__import__("random").Random(0))
    eng.start()
    eng.rng.random = lambda: 0.0
    eng.rng.choices = lambda seq, weights=None, k=1: [seq[0]]
    out = run(eng.chat("好饿"))
    assert out["event"] is not None
    assert out["event"]["phase"] == "start"
    assert out["event"]["id"] == "meal"
    assert out["event"]["tier"] == TIER_NORMAL
    assert out["save"]["active_event"]["id"] == "meal"
    assert out["save"]["event_counts"]["meal"] == 1


def test_model_decides_event(data_home):
    event = {"id": "meal", "phase": "start", "choices": ["好呀", "不想动"]}
    eng = make_engine(FakeLLM([_ok("一起去吃好不好？", delta=2, event=event)]))
    eng.start()
    out = run(eng.chat("晚上吃什么"))
    assert out["event"]["id"] == "meal"
    assert out["event"]["choices"] == ["好呀", "不想动"]


def test_event_end_clears_active(data_home):
    save = GalgameSave(started=True, affinity=30,
                       active_event=ActiveEvent(id="meal", turns=1))
    persist(save)
    eng = make_engine(FakeLLM([_ok("吃饱啦，下次再来！", delta=4,
                                   event={"id": "meal", "phase": "end"})]))
    out = run(eng.chat("很好吃"))
    assert out["event"]["phase"] == "end"
    assert out["save"]["active_event"] is None


def test_event_auto_ends_after_max_turns(data_home):
    """事件超过最大轮数强制收尾，避免无限拖延。"""
    save = GalgameSave(started=True, affinity=30,
                       active_event=ActiveEvent(id="meal", turns=2))
    persist(save)
    eng = make_engine(FakeLLM([_ok("嗯嗯", event={"id": "meal", "phase": "progress"})]))
    out = run(eng.chat("继续"))
    assert out["event"]["phase"] == "end"


def test_event_unknown_id_rejected(data_home):
    eng = make_engine(FakeLLM([_ok(event={"id": "skydiving", "phase": "start"})]))
    eng.start()
    out = run(eng.chat("找点刺激的"))
    assert out["event"] is None
    assert out["save"]["active_event"] is None


def test_model_cannot_start_locked_event(data_home):
    """好感不足时，模型不能强行开启高门槛事件。"""
    eng = make_engine(FakeLLM([_ok(event={"id": "heartlink_forever",
                                          "phase": "start"})]))
    eng.start()
    out = run(eng.chat("我们结婚吧"))
    assert out["event"] is None


# --------------------------------------------------------------------------- #
# 心链事件
# --------------------------------------------------------------------------- #
def test_heartlink_gated_by_affinity_and_chapter():
    low = GalgameSave(started=True, affinity=20, chapter=0)
    assert heartlink_ready(low) == []
    high = GalgameSave(started=True, affinity=90, chapter=3)
    assert len(heartlink_ready(high)) >= 4


def test_heartlink_cooldown_blocks_refire():
    save = GalgameSave(started=True, affinity=90, chapter=3,
                       total_turns=10, last_heartlink_turn=8)
    assert heartlink_ready(save) == []          # 冷却中
    save.last_heartlink_turn = 0                # 早已过冷却
    assert heartlink_ready(save) != []


def test_heartlink_forced_by_dice(data_home):
    save = GalgameSave(started=True, affinity=90, chapter=3)
    persist(save)
    eng = TestableEngine(FakeLLM([_ok("（心跳得好快）", delta=3)]))
    eng.rng.random = lambda: 0.0               # 心链掷骰必中
    eng.rng.choices = lambda seq, weights=None, k=1: [seq[0]]
    out = run(eng.chat("我想和你一直在一起"))
    assert out["event"]["tier"] == TIER_HEARTLINK
    assert out["save"]["last_heartlink_turn"] == out["save"]["total_turns"]


def test_heartlink_end_bonus(data_home):
    save = GalgameSave(started=True, affinity=60, chapter=1,
                       active_event=ActiveEvent(id="heartlink_bell", turns=1))
    persist(save)
    eng = make_engine(FakeLLM([_ok("（眼眶有点热）", delta=0,
                                   event={"id": "heartlink_bell", "phase": "end"})]))
    out = run(eng.chat("我也听见了"))
    assert out["affinity_delta"] == HEARTLINK_BONUS
    assert any("心链" in n for n in out["affinity_notes"])


# --------------------------------------------------------------------------- #
# 现实世界联动
# --------------------------------------------------------------------------- #
def test_world_weight_favours_matching_weather_and_time():
    rainy_night = WorldContext(weather="小雨", weather_tag="rain",
                               time_tag="night", time_period="夜晚")
    clear_morning = WorldContext(weather="晴", weather_tag="clear",
                                 time_tag="morning", time_period="清晨")
    rain_event = EVENTS["rain_umbrella"]
    morning_event = EVENTS["morning_hello"]
    assert world_weight(rain_event, rainy_night) > 1.0
    assert world_weight(rain_event, clear_morning) < 1.0
    assert world_weight(morning_event, clear_morning) > 1.0
    # 无时间/天气标签的事件不受影响
    assert world_weight(EVENTS["meal"], rainy_night) == 1.0


def test_world_weight_neutral_when_weather_unknown():
    unknown = WorldContext(time_tag="night", weather="")
    assert world_weight(EVENTS["rain_umbrella"], unknown) == 1.0


def test_world_place_text_and_region_cleanup():
    from aemeath_agent.galgame.world import _clean_region

    assert _clean_region("Sichuan Sheng") == "Sichuan"
    assert _clean_region("Zhejiang Province") == "Zhejiang"
    assert WorldContext(region="Sichuan", city="Chengdu").place_text() \
        == "Sichuan · Chengdu"
    assert WorldContext(city="Chengdu").place_text() == "Chengdu"
    assert WorldContext(city="Chengdu", region="Chengdu").place_text() == "Chengdu"
    assert WorldContext().place_text() == ""


def test_world_prompt_omits_place_when_unknown():
    text = WorldContext(time_period="夜晚").to_prompt()
    assert "玩家所在地" not in text
    assert "当地天气：未知" in text


def test_prompt_injects_world_and_dialogue_format(data_home):
    eng = make_engine(FakeLLM([]))
    save = GalgameSave(started=True, affinity=30, chapter=0)
    persist(save)
    world = WorldContext(city="杭州", weather="小雨", weather_tag="rain",
                         temperature="18°C", time_period="夜晚",
                         time_tag="night", local_time="2026-09-25 21:30",
                         weekday="星期五", season="秋")
    messages = eng._build_messages(save, None, False, world, starting=False)
    system = messages[0]["content"]
    assert "此刻的现实世界" in system
    assert "杭州" in system and "小雨" in system
    # 对话书写格式约定
    assert "「」" in system and "（）" in system and "心理活动" in system
    # 心链事件单独成节
    assert "心链事件" in system


def test_public_state_exposes_world_and_heartlink(data_home):
    eng = make_engine(FakeLLM([]))
    eng.start()
    data = eng.public_state(GalgameSave(started=True, affinity=90, chapter=3))
    assert "world" in data and "time_period" in data["world"]
    assert data["heartlink_total"] == 5
    assert data["heartlink_ready"] is True
    assert data["growth_cap"] >= 2


def test_normal_events_exclude_heartlink():
    save = GalgameSave(started=True, affinity=90, chapter=3)
    assert all(ev.tier == TIER_NORMAL for ev in normal_events(save))


# --------------------------------------------------------------------------- #
# 章节 / 结局 / 降级
# --------------------------------------------------------------------------- #
def test_chapter_transition(data_home):
    save = GalgameSave(started=True, affinity=30, chapter=0, chapter_turns=4)
    persist(save)
    eng = make_engine(FakeLLM([_ok("晚安", delta=1)]))
    out = run(eng.chat("晚安"))
    assert out["transition"] == "chapter"
    assert out["save"]["chapter"] == 1
    assert out["save"]["chapter_turns"] == 0


def test_ending_love(data_home):
    save = GalgameSave(started=True, affinity=90, chapter=3, chapter_turns=5)
    persist(save)
    eng = TestableEngine(FakeLLM(["我愿意，永远和你在一起" * 3]))
    out = run(eng.chat("我喜欢你"))
    assert out["transition"] == "ending"
    assert out["ending"]["id"] == ENDING_LOVE
    assert out["save"]["ending"] == ENDING_LOVE
    assert out["ended"] is True


def test_ending_thresholds_direct(data_home):
    eng = TestableEngine(FakeLLM(["尾声" * 20]))
    for affinity, expected in [
        (75, ENDING_LOVE), (55, ENDING_FRIEND), (20, ENDING_FADE)
    ]:
        save = GalgameSave(started=True, affinity=affinity, chapter=3,
                           chapter_turns=99)
        assert eng._settle(save)["id"] == expected


def test_chat_continues_after_ending(data_home):
    """结局不该把对话锁死：她还能陪着聊（后日谈），只是不再推进剧情。"""
    save = GalgameSave(started=True, affinity=90, chapter=3, chapter_turns=99,
                       ending=ENDING_LOVE, cg=f"ending:{ENDING_LOVE}",
                       total_turns=7)
    persist(save)
    eng = TestableEngine(FakeLLM([_ok("「我一直都在呀。」", delta=2)]))

    out = run(eng.chat("再理理我"))
    assert out["ended"] is True
    assert out["reply"] == "「我一直都在呀。」"
    assert out["transition"] is None and out["ending"] is None
    assert out["save"]["ending"] == ENDING_LOVE           # 结局不会被重新结算
    assert out["save"]["chapter"] == 3                    # 章节不再推进
    assert out["save"]["chapter_turns"] == 99
    assert out["save"]["total_turns"] == 8                # 后日谈轮次照常累计
    assert out["save"]["affinity"] == 92                  # 好感仍能正常变化
    assert out["save"]["cg"] == f"ending:{ENDING_LOVE}"   # 结局画面不被章节背景顶掉


def test_post_ending_does_not_open_events(data_home):
    """后日谈里即使骰子想开事件也不开：剧情已经结束了。"""
    eng = make_engine(FakeLLM([_ok("「嗯，我在听。」", delta=1)]))
    eng.rng.random = lambda: 0.0        # 骰子必然命中
    eng.rng.choices = lambda seq, weights=None, k=1: [seq[0]]
    persist(GalgameSave(started=True, affinity=90, chapter=3, chapter_turns=99,
                        ending=ENDING_LOVE, total_turns=7))

    out = run(eng.chat("今天过得怎么样"))
    assert out["event"] is None
    assert out["save"]["active_event"] is None
    assert out["save"]["total_turns"] == 8


def test_offline_turn_does_not_advance_story(data_home, tmp_path):
    """她没说出话的这一轮，不该由骰子替她把事件开了、把章节推下去。"""
    eng = _event_engine(_BoomLLM(), tmp_path)
    persist(GalgameSave(started=True, affinity=30, chapter=0, chapter_turns=1,
                        total_turns=3))

    out = run(eng.chat("在吗"))
    assert out["offline"] is True
    assert out["note_queued"] is True
    assert out["event"] is None
    saved = load_save()
    assert saved.active_event is None
    assert saved.total_turns == 3        # 轮次不动
    assert saved.chapter_turns == 1      # 章节进度不动
    assert saved.chapter == 0
    assert saved.affinity == 30          # 好感不动
    assert saved.pending_notes == ["在吗"]


def test_json_drift_retries_once_and_recovers(data_home):
    """模型先回正文再回 JSON：重试一次后应采用 JSON 结果。"""
    eng = make_engine(FakeLLM(["（歪头）「我在哦～」", _ok("我一直在呢", delta=2)]))
    eng.start()
    out = run(eng.chat("在吗"))
    assert out["reply"] == "我一直在呢"
    assert out["affinity_delta"] == 2
    assert out["json_drift"] is False
    assert out["offline"] is False


def test_json_drift_keeps_prose_when_retry_fails(data_home):
    """两次都不吐 JSON：保留首次正文当回复，但不算离线。"""
    eng = make_engine(FakeLLM(["今天也要开心哦！", "真的在哦，没有骗你"]))
    eng.start()
    out = run(eng.chat("在吗"))
    assert out["reply"] == "今天也要开心哦！"
    assert out["json_drift"] is True
    assert out["offline"] is False      # 不是连接失败，前端不该用警示样式
    assert out["affinity_delta"] == 0


def test_assistant_history_is_json_enveloped(data_home):
    """历史回复必须以同一套 JSON 信封进模型上下文，否则模型会跟着写散文。"""
    eng = make_engine(FakeLLM([]))
    save = GalgameSave(started=True, affinity=30)
    save.add_history("narration", "（场景旁白）")
    save.add_history("aemeath", "「你好呀」")
    save.add_history("user", "在吗")
    persist(save)
    world = WorldContext(time_period="夜晚", time_tag="night")
    messages = eng._build_messages(save, None, False, world, starting=False)

    roles = [m["role"] for m in messages]
    assert roles == ["system", "assistant", "user"]          # 旁白不进上下文
    payload = json.loads(messages[1]["content"])
    assert payload["reply"] == "「你好呀」"
    assert set(payload) == {"reply", "narration", "affinity_delta", "event"}


def test_transcript_stays_alternating(data_home):
    """旁白 / 事件行不得作为 system 插进对话，以免破坏对话模板。"""
    eng = make_engine(FakeLLM([]))
    save = GalgameSave(started=True, affinity=30)
    for role, text in [("narration", "n1"), ("aemeath", "a1"), ("user", "u1"),
                       ("event", "e1"), ("aemeath", "a2"), ("user", "u2")]:
        save.add_history(role, text)
    world = WorldContext(time_period="夜晚")
    messages = eng._build_messages(save, None, False, world, starting=False)
    assert [m["role"] for m in messages] == \
        ["system", "assistant", "user", "assistant", "user"]
    assert messages[-1]["role"] == "user"                    # 末位必须是用户回合


def test_llm_failure_is_flagged_offline(data_home):
    """调用层失败（无密钥/网络/超时）才算离线，且不产生漂移标记。"""
    class Boom:
        def complete_messages(self, *a, **k):
            raise RuntimeError("connection refused")

    eng = make_engine(Boom())
    eng.start()
    out = run(eng.chat("在吗"))
    assert out["offline"] is True
    assert out["json_drift"] is False
    assert "信号好像有点波动" in out["reply"]
    assert out["save"]["affinity"] == 20      # 失败不改好感


# --------------------------------------------------------------------------- #
# 长期记忆
# --------------------------------------------------------------------------- #
def _settings(tmp_path):
    """隔离的设置文件，避免读到本机 agent_settings.json 的阈值。"""
    return Settings(tmp_path / "agent_settings.json")


def _memory(tmp_path, *, shared=True):
    return GalgameMemory(directory=tmp_path / "gm",
                         shared_directory=(tmp_path / "shared")
                         if shared else None)


def _memory_engine(fake, tmp_path):
    eng = TestableEngine(fake, settings=_settings(tmp_path),
                         memory=_memory(tmp_path))
    eng.rng.random = lambda: 1.0
    return eng


def test_galgame_memory_dir_is_isolated(data_home, tmp_path):
    """心动模式的记录只落在自己的目录，绝不回写主控制台画像。"""
    shared = tmp_path / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "user_profile.json").write_text(
        json.dumps({"summary": "工具型画像"}, ensure_ascii=False),
        encoding="utf-8")

    eng = _memory_engine(FakeLLM([_ok("嗯嗯", delta=1)]), tmp_path)
    eng.start()
    run(eng.chat("你好"))

    assert (tmp_path / "gm" / "transcript.jsonl").exists()
    assert not (shared / "transcript.jsonl").exists()
    kept = json.loads((shared / "user_profile.json").read_text(encoding="utf-8"))
    assert kept["summary"] == "工具型画像"


def test_profile_block_injected_into_system(data_home, tmp_path):
    gm = tmp_path / "gm"
    gm.mkdir(parents=True, exist_ok=True)
    (gm / "user_profile.json").write_text(
        json.dumps({"summary": "喜欢安静", "likes": ["黑咖啡"]},
                   ensure_ascii=False), encoding="utf-8")

    eng = _memory_engine(FakeLLM([]), tmp_path)
    save = GalgameSave(started=True, affinity=30)
    messages = eng._build_messages(save, None, False,
                                   WorldContext(time_period="夜晚"),
                                   starting=False)
    system = messages[0]["content"]
    assert "长期记忆" in system
    assert "黑咖啡" in system


def test_no_memory_manager_means_no_recording(data_home, tmp_path):
    """不给记忆门面时零行为变化：不建记忆目录、无回忆、system 无画像段。"""
    eng = make_engine(FakeLLM([_ok("嗯", delta=1)]))
    eng.start()
    run(eng.chat("你好"))

    assert not (tmp_path / "galgame" / "memory").exists()
    saved = load_save()
    assert saved.memories == []
    assert saved.last_interaction_at != ""      # 互动时刻仍会记录
    messages = eng._build_messages(saved, None, False,
                                   WorldContext(time_period="夜晚"),
                                   starting=False)
    assert "你们之间的回忆" not in messages[0]["content"]


def test_chapter_transition_appends_memory(data_home, tmp_path):
    eng = _memory_engine(
        FakeLLM([_ok("晚安", delta=1), "那晚我们聊到很晚，你说有点困了。"]),
        tmp_path)
    save = GalgameSave(started=True, affinity=30, chapter=0,
                       chapter_turns=CHAPTERS[0].turns - 1)
    persist(save)

    out = run(eng.chat("晚安"))
    assert out["transition"] == "chapter"
    saved = load_save()
    assert len(saved.memories) == 1
    assert saved.memory_cursor == saved.total_turns
    assert "聊到很晚" in saved.memories[0]["text"]


def test_interval_memory_trigger(data_home, tmp_path):
    """没跨章节但聊满 MEMORY_INTERVAL 轮，也会压缩出一段回忆。"""
    eng = _memory_engine(FakeLLM([_ok("嗯", delta=0), "我们聊了很多。"]), tmp_path)
    save = GalgameSave(started=True, affinity=30, chapter=0, chapter_turns=0,
                       total_turns=MEMORY_INTERVAL, memory_cursor=0)
    persist(save)

    run(eng.chat("继续"))
    assert len(load_save().memories) == 1


def test_memory_fallback_on_llm_error(data_home, tmp_path):
    """压缩用的 LLM 调用失败时，用确定性模板兜底，仍然留下回忆。"""
    class FlakyLLM:
        def __init__(self, first):
            self._first = first
            self.calls = 0

        def complete_messages(self, messages, model=None, temperature=None,
                              max_tokens=None):
            self.calls += 1
            if self.calls == 1:
                return self._first
            raise RuntimeError("boom")

    eng = _memory_engine(FlakyLLM(_ok("晚安", delta=1)), tmp_path)
    save = GalgameSave(started=True, affinity=30, chapter=0,
                       chapter_turns=CHAPTERS[0].turns - 1)
    persist(save)

    out = run(eng.chat("晚安"))
    assert out["transition"] == "chapter"
    saved = load_save()
    assert len(saved.memories) == 1
    assert "聊了大约" in saved.memories[0]["text"]


def test_memory_length_capped_and_total_limited(data_home, tmp_path):
    eng = _memory_engine(FakeLLM(["回忆" * 200]), tmp_path)
    save = GalgameSave(started=True, affinity=30)
    save.add_history("user", "你好")
    save.add_history("aemeath", "嗯")
    save.total_turns = 3
    world = WorldContext(time_period="夜晚")

    eng._compress_memory(save, world, "transition")
    assert len(save.memories[-1]["text"]) <= MEMORY_CHAR_LIMIT

    save.memories = [{"turn": i, "chapter": 0, "text": f"m{i}", "at": ""}
                     for i in range(40)]
    save.history = [{"role": "user", "content": "再聊聊"}]
    save.memory_cursor = 0
    save.total_turns = 5
    eng._compress_memory(save, world, "interval")
    assert len(save.memories) == MEMORY_MAX_ITEMS


def test_memories_injected_and_limited(data_home):
    """只注入最近 MEMORY_KEEP 条回忆，避免 system prompt 无限膨胀。"""
    eng = make_engine(FakeLLM([]))
    save = GalgameSave(started=True, affinity=30)
    save.memories = [{"turn": i, "chapter": 0, "text": f"回忆{i}", "at": ""}
                     for i in range(12)]

    messages = eng._build_messages(save, None, False,
                                   WorldContext(time_period="夜晚"),
                                   starting=False)
    system = messages[0]["content"]
    assert "【你们之间的回忆】" in system
    first = 12 - MEMORY_KEEP
    assert f"回忆{first}" in system and "回忆11" in system
    assert f"回忆{first - 1}" not in system


def test_memory_injection_keeps_roles_alternating(data_home):
    """注入回忆只改 system 字符串，messages 的 roles 结构不得变化。"""
    eng = make_engine(FakeLLM([]))
    save = GalgameSave(started=True, affinity=30)
    save.add_history("narration", "n")
    save.add_history("aemeath", "a")
    save.add_history("user", "u")
    save.memories = [{"turn": 1, "chapter": 0, "text": "回忆", "at": ""}]

    messages = eng._build_messages(save, None, False,
                                   WorldContext(time_period="夜晚"),
                                   starting=False)
    assert [m["role"] for m in messages] == ["system", "assistant", "user"]


def test_old_save_without_new_fields_loads(data_home):
    """旧存档（无记忆/互动字段）必须能继续读取并取默认值。"""
    path = save_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"started": True, "affinity": 33, "chapter": 1}),
                    encoding="utf-8")

    save = load_save()
    assert save.affinity == 33 and save.chapter == 1
    assert save.memories == []
    assert save.memory_cursor == 0
    assert save.last_interaction_at == ""
    assert save.proactive_count == 0


# --------------------------------------------------------------------------- #
# 主动陪伴
# --------------------------------------------------------------------------- #
def _ago(**kwargs) -> str:
    """距今指定时间差的 ISO 字符串（如 hours=8 → 8 小时前）。"""
    return (datetime.now() - timedelta(**kwargs)).isoformat(timespec="seconds")


def _proactive_save(**kwargs) -> GalgameSave:
    base = dict(started=True, affinity=30, last_interaction_at=_ago(hours=8))
    base.update(kwargs)
    return GalgameSave(**base)


def test_proactive_skips_when_recent(data_home):
    eng = make_engine(FakeLLM([]))
    persist(GalgameSave(started=True, affinity=30,
                        last_interaction_at=_ago(minutes=1)))

    out = run(eng.proactive("resume"))
    assert out["skipped"] is True and out["reason"] == "too_soon"
    out2 = run(eng.proactive("idle"))
    assert out2["skipped"] is True and out2["reason"] == "just_talked"


def test_proactive_resume_after_gap(data_home):
    eng = make_engine(FakeLLM([_ok("「你回来啦」", delta=5)]))
    persist(_proactive_save())

    out = run(eng.proactive("resume"))
    assert out["proactive"] is True
    assert out["reason"] == "resume"
    assert out["reply"] == "「你回来啦」"
    assert out["affinity_delta"] == 0
    assert out["save"]["affinity"] == 30


def test_proactive_cooldown(data_home):
    eng = make_engine(FakeLLM([]))
    persist(_proactive_save(last_proactive_at=_ago(minutes=5)))

    out = run(eng.proactive("resume"))
    assert out["skipped"] is True and out["reason"] == "cooldown"


def test_proactive_daily_cap_and_reset(data_home):
    today = datetime.now().strftime("%Y-%m-%d")
    eng = make_engine(FakeLLM([_ok("「嘿」")]))
    persist(_proactive_save(last_proactive_at=_ago(hours=2),
                            proactive_count=PROACTIVE_DAILY_CAP,
                            proactive_count_date=today))

    out = run(eng.proactive("resume"))
    assert out["skipped"] is True and out["reason"] == "daily_cap"

    # 跨日归零后可以重新主动
    save = load_save()
    save.proactive_count_date = "2000-01-01"
    persist(save)
    out2 = run(eng.proactive("resume"))
    assert out2.get("proactive") is True
    assert out2["save"]["proactive_count"] == 1


def test_proactive_does_not_change_affinity_or_turn(data_home):
    """主动轮不改好感、不推进轮数/章节，避免被反复调用刷数值。"""
    eng = make_engine(FakeLLM([_ok("「在哦」", delta=8)]))
    persist(_proactive_save())

    out = run(eng.proactive("resume"))
    assert out["save"]["affinity"] == 30
    assert out["save"]["total_turns"] == 0
    assert out["save"]["chapter"] == 0
    assert out["affinity_notes"] == ["她主动搭话，不计好感"]


def test_proactive_offline_uses_greeting_template(data_home, monkeypatch):
    from aemeath_agent.galgame import engine as engine_mod
    monkeypatch.setattr(
        engine_mod, "get_world",
        lambda force=False: WorldContext(time_tag="morning", time_period="清晨"))

    class Boom:
        def complete_messages(self, *a, **k):
            raise RuntimeError("connection refused")

    eng = make_engine(Boom())
    persist(_proactive_save())
    out = run(eng.proactive("resume"))
    assert out["offline"] is True
    assert out["proactive"] is True
    assert "早" in out["reply"]


def test_proactive_drops_heartlink_event(data_home):
    """主动轮只允许开启普通事件：心链必须由玩家郑重回应、走 chat 通道。"""
    hl = next(ev for ev in EVENTS.values() if ev.is_heartlink)
    eng = make_engine(FakeLLM([
        _ok("（她认真地看着你）", event={"id": hl.id, "phase": "start"})]))
    persist(_proactive_save(affinity=90, chapter=3))

    out = run(eng.proactive("resume"))
    assert out["event"] is None
    assert out["save"]["active_event"] is None


def test_proactive_on_ended_save_returns_skipped(data_home):
    eng = make_engine(FakeLLM([]))
    persist(_proactive_save(ending=ENDING_LOVE))

    out = run(eng.proactive("resume"))
    assert out["skipped"] is True and out["reason"] == "ended"


# --------------------------------------------------------------------------- #
# 记忆向量检索（可插拔后端）
# --------------------------------------------------------------------------- #
_PLOT_DOCS = [
    "我们又聊了一会儿天气，随便说了些日常。",
    "那天下大雨，你撑着伞来车站接我。",
    "第二天我们还在讨论晚饭吃什么。",
]


def test_hashing_retriever_prefers_relevant_document():
    """本地实现：最相关的那条必须排在最前。"""
    top = HashingRetriever().rank("外面下雨了，你带伞了吗", _PLOT_DOCS, top_k=1)
    assert top == [1]


def test_hashing_retriever_falls_back_to_recent():
    """空输入 / 无命中时退化为最近若干条，宁可降级也不能失忆。"""
    retriever = HashingRetriever()
    assert retriever.rank("", ["甲甲", "乙乙", "丙丙"], top_k=2) == [1, 2]
    assert retriever.rank("毫不相关的内容", ["甲甲", "乙乙", "丙丙"],
                          top_k=2) == [1, 2]
    assert retriever.rank("随便", [], top_k=3) == []
    assert retriever.rank("随便", ["甲"], top_k=5) == [0]


def test_tokenize_covers_han_and_ascii():
    tokens = tokenize("雨天 room_101")
    assert "雨" in tokens and "雨天" in tokens          # 单字 + 相邻双字
    assert "room_101" in tokens                          # ASCII 整词
    assert "a" not in tokenize("a b")                    # 单字母不参与，避免噪声


def test_build_retriever_defaults_to_local_and_upgrades():
    """没配 embedding 就用本地实现；配齐三项才尝试远程。"""
    class _Cfg:
        def __init__(self, cfg):
            self._cfg = cfg

        def get_embedding(self):
            return self._cfg

    assert isinstance(build_retriever(None), HashingRetriever)
    assert isinstance(build_retriever(_Cfg({})), HashingRetriever)
    assert isinstance(build_retriever(_Cfg({"api_base": "https://x/v1"})),
                      HashingRetriever)                       # 缺 model/key
    remote = build_retriever(_Cfg({"api_base": "https://x/v1", "model": "m",
                                   "api_key": "k"}))
    assert isinstance(remote, EmbeddingRetriever)
    assert remote.disabled is False


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeEmbeddingHTTP:
    """假 embedding 端点：含「雨 / 伞」的文本给 [1,0,0]，其余给 [0,1,0]。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        inputs = list((json or {}).get("input") or [])
        return _Resp({"data": [
            {"embedding": [1.0, 0.0, 0.0] if ("雨" in t or "伞" in t)
             else [0.0, 1.0, 0.0]} for t in inputs]})


def test_embedding_retriever_uses_remote_vectors():
    http = _FakeEmbeddingHTTP()
    retriever = EmbeddingRetriever(api_base="https://api.example.com/v1",
                                   model="bge-m3", api_key="k", post_fn=http)

    assert retriever.rank("外面下雨了", _PLOT_DOCS, top_k=1) == [1]
    assert retriever.disabled is False
    assert http.calls == 1          # 查询与候选文档一次性批量编码


def test_embedding_retriever_degrades_to_local_on_failure():
    """远程失败必须降级到本地，而不是让检索整体失效。"""
    def boom(*args, **kwargs):
        raise RuntimeError("no network")

    retriever = EmbeddingRetriever(api_base="https://api.example.com/v1",
                                   model="bge-m3", api_key="k", post_fn=boom)
    assert retriever.rank("外面下雨了，你带伞了吗", _PLOT_DOCS, top_k=1) == [1]
    assert retriever.disabled is True
    assert retriever.failure == "RuntimeError"
    # 已降级后再调用不再发请求，也不会抛错
    assert retriever.rank("外面下雨了，你带伞了吗", _PLOT_DOCS, top_k=1) == [1]


def test_embedding_retriever_bad_response_degrades():
    class Bad:
        def __call__(self, url, json=None, headers=None, timeout=None):
            return _Resp({"data": []})

    retriever = EmbeddingRetriever(api_base="https://api.example.com/v1",
                                   model="m", api_key="k", post_fn=Bad())
    assert retriever.rank("外面下雨了", _PLOT_DOCS, top_k=1) == [1]
    assert retriever.disabled is True


def test_engine_uses_injected_retriever(data_home):
    """检索后端可插拔：构造函数注入的实现必须真的被调用。"""
    class SpyRetriever(Retriever):
        name = "spy"

        def __init__(self):
            self.calls = []

        def rank(self, query, documents, top_k, boosts=None):
            self.calls.append((query, list(documents), top_k))
            return [0]

    spy = SpyRetriever()
    eng = TestableEngine(FakeLLM([]), retriever=spy)
    save = GalgameSave(started=True, affinity=30)
    save.memories = [{"turn": i, "chapter": 0, "text": f"m{i}", "at": ""}
                     for i in range(12)]
    save.add_history("user", "今天想做点什么")

    system = eng._build_messages(save, None, False,
                                 WorldContext(time_period="夜晚"),
                                 starting=False)[0]["content"]
    assert len(spy.calls) == 1
    assert spy.calls[0][0] == "今天想做点什么"      # 检索用的就是玩家这句话
    assert spy.calls[0][2] == MEMORY_KEEP
    assert "m0" in system


def test_engine_defaults_to_local_retriever_without_embedding_config(data_home):
    """设置里没有 embedding 段时，引擎默认装配本地实现（不联网）。"""
    eng = make_engine(FakeLLM([]))
    assert isinstance(eng.retriever, HashingRetriever)
    assert eng.retriever.name == "hashing"


# --------------------------------------------------------------------------- #
# 事件级记忆
# --------------------------------------------------------------------------- #
def _event_engine(fake, tmp_path):
    """必然掷中事件的记忆引擎（随机数固定为 0）。"""
    eng = _memory_engine(fake, tmp_path)
    eng.rng.random = lambda: 0.0
    eng.rng.choices = lambda seq, weights=None, k=1: [seq[0]]
    return eng


def test_event_memory_written_on_start_and_end(data_home, tmp_path):
    eng = _event_engine(FakeLLM([
        _ok("走吧，正好我也饿了", delta=1),
        _ok("嗯，那就回家啦", event={"phase": "end"}),
    ]), tmp_path)
    eng.start()

    first = run(eng.chat("出去走走"))
    assert first["event"]["phase"] == "start"
    second = run(eng.chat("回家吧"))
    assert second["event"]["phase"] == "end"

    events = [m for m in load_save().memories
              if m.get("kind") == MEMORY_KIND_EVENT]
    assert len(events) == 2
    assert events[0]["tags"] and events[0]["entities"]
    assert events[0]["entities"][0] in events[0]["text"]
    # 收尾的事实比开启更值得记住
    assert events[1]["salience"] > events[0]["salience"]


def test_heartlink_memory_has_top_salience(data_home, tmp_path):
    eng = _event_engine(FakeLLM([_ok("（她认真地看着你）", delta=1)]), tmp_path)
    persist(GalgameSave(started=True, affinity=90, chapter=3, chapter_turns=0))

    out = run(eng.chat("我有些话想认真跟你说"))
    assert out["event"]["tier"] == TIER_HEARTLINK

    events = [m for m in load_save().memories
              if m.get("kind") == MEMORY_KIND_EVENT]
    assert events and events[0]["salience"] == 1.0
    assert "心链" in events[0]["tags"]


def test_promise_memory_written_on_agreement(data_home, tmp_path):
    eng = _memory_engine(
        FakeLLM([_ok("那我们说好，下次一起去海边。", delta=2)]), tmp_path)
    eng.start()
    run(eng.chat("我们约定好下次一起去海边"))

    promises = [m for m in load_save().memories
                if m.get("kind") == MEMORY_KIND_PROMISE]
    assert len(promises) == 1
    assert "下次一起去海边" in promises[0]["text"]
    assert promises[0]["salience"] == SALIENCE_PROMISE
    assert "约定" in promises[0]["tags"]


def test_no_promise_memory_for_ordinary_chat(data_home, tmp_path):
    """没有约定语义的日常闲聊不该写约定记忆。"""
    eng = _memory_engine(FakeLLM([_ok("今天过得怎么样？", delta=1)]), tmp_path)
    eng.start()
    run(eng.chat("今天想你了"))
    assert load_save().memories == []


def test_event_memory_skipped_without_memory_facade(data_home):
    """不给记忆门面时零行为变化：事件照常发生，但一条记忆都不记。"""
    eng = make_engine(FakeLLM([_ok("走吧", delta=1)]))
    eng.rng.random = lambda: 0.0
    eng.rng.choices = lambda seq, weights=None, k=1: [seq[0]]
    eng.start()
    out = run(eng.chat("出去走走"))
    assert out["event"] is not None
    assert load_save().memories == []


# --------------------------------------------------------------------------- #
# 结构化记忆的注入与兼容
# --------------------------------------------------------------------------- #
def test_retrieval_injects_old_but_relevant_memory(data_home):
    """最旧的一条只要相关就该被想起来，而不是被「取最近 N 条」挤掉。"""
    eng = make_engine(FakeLLM([]))
    save = GalgameSave(started=True, affinity=30)
    save.memories = [{"turn": i, "chapter": 0, "text": f"我们又聊了一会儿天气{i}",
                      "at": ""} for i in range(12)]
    save.memories[0] = {"turn": 0, "chapter": 0,
                        "text": "那天下大雨，你撑着伞来车站接我", "at": ""}
    save.add_history("user", "今天又下雨了，你带伞了吗")

    system = eng._build_messages(save, None, False,
                                 WorldContext(time_period="夜晚"),
                                 starting=False)[0]["content"]
    assert "撑着伞来车站接我" in system
    section = system.split("【你们之间的回忆】")[1].split("\n\n")[0]
    lines = [line for line in section.splitlines() if line.startswith("- ")]
    assert 0 < len(lines) <= MEMORY_KEEP


def test_structured_memory_label_injected(data_home):
    """约定 / 经历类记忆带类型前缀，让模型看得出分量。"""
    eng = make_engine(FakeLLM([]))
    save = GalgameSave(started=True, affinity=30)
    save.memories = [
        {"turn": 1, "chapter": 0, "kind": MEMORY_KIND_PROMISE,
         "text": "你和我约好了：下次一起去看海", "salience": 0.9,
         "tags": ["约定"], "entities": ["看海"], "at": ""},
        {"turn": 2, "chapter": 0, "kind": MEMORY_KIND_EVENT,
         "text": "我们一起开始了「雨天共伞」这件事。", "salience": 0.6,
         "tags": ["rain"], "entities": ["雨天共伞"], "at": ""},
    ]
    system = eng._build_messages(save, None, False,
                                 WorldContext(time_period="夜晚"),
                                 starting=False)[0]["content"]
    assert "约定·你和我约好了" in system
    assert "经历·我们一起开始了" in system


def test_legacy_memory_entries_are_normalized():
    """老存档的裸记忆（无 kind/salience/tags）必须能补齐后正常使用。"""
    assert _norm_memory({"text": "很久以前的事"}) == {
        "kind": MEMORY_KIND_PLOT, "text": "很久以前的事",
        "salience": 0.5, "tags": [], "entities": []}
    broken = _norm_memory({"text": "x", "kind": "unknown", "salience": "坏值",
                           "tags": "不是列表", "entities": None})
    assert broken["kind"] == MEMORY_KIND_PLOT
    assert broken["salience"] == 0.5
    assert broken["tags"] == [] and broken["entities"] == []
    assert _norm_memory(None)["text"] == ""


def test_legacy_save_memories_still_playable(data_home, tmp_path):
    """只有 text 的旧回忆：检索、注入、继续追加新记忆都不报错。"""
    eng = _memory_engine(FakeLLM([_ok("嗯", delta=1)]), tmp_path)
    save = GalgameSave(started=True, affinity=30)
    save.memories = [{"turn": 0, "chapter": 0, "text": "我们聊到很晚", "at": ""}]
    persist(save)

    out = run(eng.chat("今天想你了"))
    assert out["reply"] == "嗯"
    saved = load_save()
    assert saved.memories[0]["text"] == "我们聊到很晚"      # 原条目不动
    assert len(saved.memories) == 1


# --------------------------------------------------------------------------- #
# 记忆录 / 时间轴（公开视图）
# --------------------------------------------------------------------------- #
def test_history_entries_carry_timestamp():
    """历史条目带写入时刻，前端据此画时间轴分隔线。"""
    save = GalgameSave()
    save.add_history("user", "在吗")
    assert save.history[0]["role"] == "user"
    assert datetime.fromisoformat(save.history[0]["at"])     # 可解析的本地时间

    save.add_history("aemeath", "在的", at="2026-01-01T08:00:00")
    assert save.history[1]["at"] == "2026-01-01T08:00:00"

    save.add_history("narration", "   ")                     # 空内容不落历史
    assert len(save.history) == 2


def test_public_state_exposes_memory_log_and_notes(data_home):
    """回忆录与信箱随状态一起下发，旧条目会被补齐成完整结构。"""
    eng = make_engine(FakeLLM([]))
    save = GalgameSave(
        started=True, affinity=30,
        memories=[
            {"turn": 3, "chapter": 0, "text": "我们说好一起看海",
             "at": "2026-10-27T21:30:00"},                       # 旧格式：无 kind
            {"turn": 5, "chapter": 1, "kind": MEMORY_KIND_EVENT,
             "text": "我们一起开始了「雨天共伞」这件事。", "salience": 1.0,
             "tags": ["rain"], "entities": ["雨天共伞"], "at": ""},
        ],
        pending_notes=["在吗", "记得吃饭"])

    data = eng.public_state(save)
    log = data["memory_log"]
    assert len(log) == 2
    assert log[0]["kind"] == MEMORY_KIND_PLOT and log[0]["label"] == "回忆"
    assert log[0]["turn"] == 3 and log[0]["at"] == "2026-10-27T21:30:00"
    assert log[1]["label"] == "经历" and log[1]["entities"] == ["雨天共伞"]
    assert data["notes"] == ["在吗", "记得吃饭"]


# --------------------------------------------------------------------------- #
# 离线留言（信箱）
# --------------------------------------------------------------------------- #
class _BoomLLM:
    def complete_messages(self, *args, **kwargs):
        raise RuntimeError("connection refused")


def test_offline_message_queued_as_note(data_home, tmp_path):
    """她没信号时，玩家的话先进信箱，而不是石沉大海。"""
    eng = _memory_engine(_BoomLLM(), tmp_path)
    eng.start()
    out = run(eng.chat("我先睡了，晚安"))

    assert out["offline"] is True
    assert out["note_queued"] is True
    assert load_save().pending_notes == ["我先睡了，晚安"]


def test_pending_notes_injected_then_cleared(data_home, tmp_path):
    """她重新上线时读到信箱；读过即清空，不会每轮重复提醒。"""
    eng = _memory_engine(FakeLLM([_ok("我看到你的留言了", delta=1)]), tmp_path)
    persist(GalgameSave(started=True, affinity=30,
                        pending_notes=["我先睡了，晚安", "记得吃饭"]))

    out = run(eng.chat("在吗"))
    assert out["offline"] is False
    system = eng._fake.last_messages[0]["content"]
    assert "记得吃饭" in system
    assert "她不在时你留下的留言" in system
    assert load_save().pending_notes == []


def test_pending_notes_capped():
    """信箱有上限：超出的丢最旧的，避免无限膨胀。"""
    save = GalgameSave()
    for i in range(8):
        _queue_note(save, f"留言{i}")
    assert save.pending_notes == [f"留言{i}" for i in range(8 - PENDING_NOTE_MAX, 8)]
    assert _queue_note(save, "   ") is False


def test_note_survives_until_she_reads_it(data_home, tmp_path):
    """连续离线留言会累积；她在下一次成功回复时才一次性读完。"""
    eng = _memory_engine(_BoomLLM(), tmp_path)
    eng.start()
    run(eng.chat("第一条"))
    run(eng.chat("第二条"))
    assert load_save().pending_notes == ["第一条", "第二条"]

    # 换成能用的模型：下一轮她读信并清空
    eng._fake = FakeLLM([_ok("我都看到啦")])
    out = run(eng.chat("在吗"))
    assert out["offline"] is False
    assert load_save().pending_notes == []
    assert "第一条" in eng._fake.last_messages[0]["content"]


def test_proactive_clears_notes_after_success(data_home):
    eng = make_engine(FakeLLM([_ok("「我看到留言了」")]))
    persist(_proactive_save(pending_notes=["我先去睡了"]))

    out = run(eng.proactive("resume"))
    assert out.get("proactive") is True
    assert load_save().pending_notes == []


def test_proactive_keeps_notes_when_offline(data_home):
    eng = make_engine(_BoomLLM())
    persist(_proactive_save(pending_notes=["在吗"]))

    out = run(eng.proactive("resume"))
    assert out["offline"] is True
    assert load_save().pending_notes == ["在吗"]


def test_old_save_without_pending_notes_loads(data_home):
    """旧存档没有 pending_notes 字段：读出空信箱，页面照常可玩。"""
    path = save_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"started": True, "affinity": 40}),
                    encoding="utf-8")

    save = load_save()
    assert save.pending_notes == []


# --------------------------------------------------------------------------- #
# 画像阈值 / 记忆裁剪 / 收藏
# --------------------------------------------------------------------------- #
def test_galgame_uses_its_own_summary_threshold():
    """心动模式的画像阈值必须是自己的尺度，而不是主控制台的百万级默认值。"""
    assert 0 < MEMORY_SUMMARY_TOKENS <= 20_000


def test_image_summary_triggered(monkeypatch):
    """累计 token 过阈值时，真的会去总结「她眼中的你」画像。"""
    calls = {}

    class _FakeMemory:
        def record(self, user_text, assistant_text):
            return MEMORY_SUMMARY_TOKENS

        def summarize(self, client, model=None):
            calls["summarized"] = True

    eng = GalgameEngine(memory=_FakeMemory())
    eng._client = lambda: (object(), "fake")
    eng._record_memory("你好", "在的")
    assert calls.get("summarized") is True


def test_trim_keeps_starred_and_salient():
    """裁剪不能把收藏或高显著度（心链/约定）的记忆挤掉。"""
    save = GalgameSave(
        memories=[{"turn": i, "chapter": 0, "text": f"日常{i}", "at": ""}
                  for i in range(MEMORY_MAX_ITEMS)],
        starred=["不会用到"])
    # 塞进三条更该被记住的：一条心链、一条约定、一条被收藏的日常
    save.memories[0] = {"turn": 0, "chapter": 0, "kind": MEMORY_KIND_EVENT,
                        "text": "心链：雨天共伞", "salience": 1.0, "at": ""}
    save.memories[1] = {"turn": 1, "chapter": 0, "kind": MEMORY_KIND_PROMISE,
                        "text": "约定：一起去看海", "salience": 0.9, "at": ""}
    save.starred = [_memory_mid("日常5")]
    save.memories.append({"turn": 99, "chapter": 0, "text": "最新一条", "at": ""})

    _trim_memories(save)
    texts = [m["text"] for m in save.memories]
    assert len(save.memories) == MEMORY_MAX_ITEMS
    assert "心链：雨天共伞" in texts
    assert "约定：一起去看海" in texts
    assert "日常5" in texts          # 收藏的日常保住了
    assert "日常0" not in texts       # 毫无特别的旧日常被淘汰


def test_trim_clears_stale_starred():
    """已经被淘汰的记忆，其收藏标记也要一起清掉，避免 starred 无限增长。"""
    save = GalgameSave(memories=[{"turn": 1, "chapter": 0, "text": "还在",
                                  "at": ""}],
                       starred=[_memory_mid("早就没了"), _memory_mid("还在")])
    _trim_memories(save)
    assert save.starred == [_memory_mid("还在")]


def test_toggle_star_roundtrip(data_home):
    eng = make_engine(FakeLLM([]))
    persist(GalgameSave(started=True, affinity=30, memories=[
        {"turn": 1, "chapter": 0, "kind": MEMORY_KIND_PROMISE,
         "text": "你和我约好了：下次一起去看海", "salience": 0.9, "at": ""}]))

    mid = _memory_mid("你和我约好了：下次一起去看海")
    out = eng.toggle_star(mid, True)
    assert out["memory_log"][0]["star"] is True
    assert load_save().starred == [mid]

    out2 = eng.toggle_star(mid, False)
    assert out2["memory_log"][0]["star"] is False
    assert load_save().starred == []


def test_toggle_star_ignores_unknown_mid(data_home):
    """指纹对不上任何记忆时不该污染存档。"""
    eng = make_engine(FakeLLM([]))
    persist(GalgameSave(started=True, affinity=30))
    eng.toggle_star("deadbeef00", True)
    assert load_save().starred == []


def test_memory_log_exposes_mid_and_star(data_home):
    eng = make_engine(FakeLLM([]))
    persist(GalgameSave(started=True, affinity=30, memories=[
        {"turn": 1, "chapter": 0, "text": "第一条", "at": ""}],
        starred=[_memory_mid("第一条")]))

    log = eng.public_state(load_save())["memory_log"]
    assert log[0]["mid"] == _memory_mid("第一条")
    assert log[0]["star"] is True


# --------------------------------------------------------------------------- #
# 往期周目（归档）
# --------------------------------------------------------------------------- #
def test_reset_archives_instead_of_wiping(data_home, tmp_path):
    """重新开始会归档当前周目，回忆不再被直接抹掉。"""
    eng = make_engine(FakeLLM([]))
    persist(GalgameSave(started=True, affinity=62, chapter=2, total_turns=17,
                        ending=ENDING_LOVE,
                        memories=[{"turn": 9, "chapter": 2, "text": "看海那天",
                                   "at": ""}]))

    data = eng.reset()
    assert data["archived"] is True
    assert data["save"]["started"] is True
    assert data["save"]["total_turns"] == 0            # 新周目从头开始
    assert data["save"]["memories"] == []

    rows = eng.archives()
    assert len(rows) == 1
    assert rows[0]["turns"] == 17
    assert rows[0]["affinity"] == 62
    assert rows[0]["ending"] == ENDINGS[ENDING_LOVE]["title"]
    assert rows[0]["memory_count"] == 1
    assert rows[0]["last_memory"] == "看海那天"
    assert rows[0]["archived_at"]


def test_reset_without_progress_archives_nothing(data_home):
    """全新存档没什么可归档时，不该凭空造一份空归档。"""
    eng = make_engine(FakeLLM([]))
    data = eng.reset()
    assert data["archived"] is False
    assert eng.archives() == []


def test_archives_keeps_only_recent(tmp_path, monkeypatch):
    """归档数量有上限，超出后淘汰最旧的。"""
    monkeypatch.setenv("AEMEATH_AGENT_DATA_HOME", str(tmp_path))
    for i in range(ARCHIVE_KEEP + 3):
        persist(GalgameSave(started=True, affinity=30, total_turns=i))
        archive_current()
    assert len(iter_archives()) == ARCHIVE_KEEP


def test_archives_capped_and_sorted(tmp_path, monkeypatch):
    """往期列表最新在前，且损坏的归档文件被跳过。"""
    monkeypatch.setenv("AEMEATH_AGENT_DATA_HOME", str(tmp_path))
    persist(GalgameSave(started=True, affinity=30, total_turns=1))
    archive_current()

    directory = archive_dir()
    (directory / "save_19990101_000000.json").write_text("{ 坏掉的 json",
                                                         encoding="utf-8")
    rows = iter_archives()
    assert len(rows) == 1                       # 坏文件被跳过
    assert rows[0]["total_turns"] == 1


def test_engine_archives_after_reset(data_home):
    """接口层：reset 之后 archives() 能读到刚归档的那一局。"""
    eng = make_engine(FakeLLM([]))
    persist(GalgameSave(started=True, affinity=44, total_turns=6))

    eng.reset()
    rows = eng.archives()
    assert len(rows) == 1 and rows[0]["turns"] == 6

    # start() 不应重复归档（它只是继续当前周目）
    eng.start()
    assert len(eng.archives()) == 1


# --------------------------------------------------------------------------- #
# 存档健康度：历史裁剪
# --------------------------------------------------------------------------- #
def _long_save(limit=HISTORY_MAX_ITEMS, extra=40):
    """造一份超出上限的存档（对话严格按 user/aemeath 交替）。"""
    save = GalgameSave(started=True, affinity=40)
    for i in range((limit + extra) // 2):
        save.add_history("user", f"玩家{i}")
        save.add_history("aemeath", f"爱弥斯{i}")
    save.total_turns = (limit + extra) // 2
    save.memory_cursor = 100
    return save


def test_trim_history_caps_and_keeps_tail():
    save = _long_save(limit=50, extra=10)
    before = len(save.history)
    dropped = save.trim_history(limit=50)

    assert dropped == before - 50
    assert len(save.history) == 50
    # 保住的是最近那段：最后一条仍在，最早那些被丢掉
    assert save.history[-1]["content"].startswith("爱弥斯")
    assert all(i["content"] != "玩家0" for i in save.history)


def test_trim_history_aligns_to_user_turn():
    """从玩家发言处下刀，避免历史以半句旁白或她的台词开头。"""
    save = GalgameSave(started=True)
    save.add_history("narration", "旁白0")
    for i in range(30):
        save.add_history("user", f"u{i}")
        save.add_history("narration", f"n{i}")
        save.add_history("aemeath", f"a{i}")
    save.trim_history(limit=20)

    assert len(save.history) <= 20
    assert save.history[0]["role"] == "user"


def test_trim_history_shifts_memory_cursor():
    """游标同时是历史下标：裁剪后必须同步前移，否则会重复压缩旧剧情。"""
    save = GalgameSave(started=True)
    for i in range(100):
        save.add_history("user", f"u{i}")
        save.add_history("aemeath", f"a{i}")
    save.memory_cursor = 80

    dropped = save.trim_history(limit=40)
    assert dropped == 160
    # 原来下标 80 的那条，裁剪后落在 80 - 160 -> 0（不能再小）
    assert save.memory_cursor == 0

    save2 = GalgameSave(started=True)
    for i in range(100):
        save2.add_history("user", f"u{i}")
        save2.add_history("aemeath", f"a{i}")
    save2.memory_cursor = 190          # 已压到很靠后
    save2.trim_history(limit=40)
    assert save2.memory_cursor == 190 - 160


def test_trim_history_under_limit_is_noop():
    save = GalgameSave(started=True)
    save.add_history("user", "只有一条")
    assert save.trim_history(limit=10) == 0
    assert len(save.history) == 1
    assert save.memory_cursor == 0


def test_chat_trims_history_in_save_file(data_home):
    """跑一轮真实对话后，落盘的历史不会超过上限。"""
    save = _long_save(limit=HISTORY_MAX_ITEMS, extra=30)
    persist(save)
    eng = make_engine(FakeLLM([_ok("嗯", delta=1)]))
    eng._world = None

    run(eng.chat("最后一句"))
    kept = load_save().history
    assert len(kept) <= HISTORY_MAX_ITEMS
    assert kept[-2]["content"] == "最后一句"
    assert kept[-1]["content"] == "嗯"


def test_trim_preserves_memories_and_stars(data_home):
    """裁剪历史不能波及回忆与收藏——长期连续性靠它们。"""
    save = _long_save(limit=HISTORY_MAX_ITEMS, extra=30)
    save.memories = [{"turn": 3, "chapter": 0, "text": "约定：看海", "at": ""}]
    save.starred = [_memory_mid("约定：看海")]
    persist(save)

    eng = make_engine(FakeLLM([_ok("嗯", delta=1)]))
    run(eng.chat("最后一句"))

    after = load_save()
    assert [m["text"] for m in after.memories] == ["约定：看海"]
    assert after.starred == [_memory_mid("约定：看海")]


def test_trim_keeps_recent_turns_for_prompt(data_home):
    """裁剪后仍要够 HISTORY_LIMIT 条供模型注入，别把上下文压没了。"""
    save = _long_save(limit=HISTORY_MAX_ITEMS, extra=30)
    persist(save)

    eng = make_engine(FakeLLM([_ok("嗯", delta=1)]))
    run(eng.chat("最后一句"))
    saved = load_save()
    messages = eng._build_messages(saved, None, False,
                                   WorldContext(time_period="夜晚"),
                                   starting=False)
    # 一轮结束后末条是她的回复，但注入窗口仍然被填满
    assert len(messages) - 1 >= HISTORY_LIMIT - 4
    assert any(m["content"] == "最后一句" for m in messages)