"""文本编辑能力测试（fixer/text_edit.py + 两个编辑原语）。

覆盖四件事：**改得准**、**幂等**、**保持原格式**、**改坏能回滚**。
"""

from __future__ import annotations

import json
from pathlib import Path

from gamedoctor.analyzer import peek_text_file
from gamedoctor.errors import TextEditError
from gamedoctor.fixer.backup.manager import BackupManager
from gamedoctor.fixer.executor.transactional import ExecutionMode, TransactionalExecutor
from gamedoctor.fixer.primitives import get as get_primitive
from gamedoctor.fixer.text_edit import (
    replace_literal,
    replace_regex,
    set_ini_value,
    set_json_value,
)
from gamedoctor.models import ActionLevel, RepairAction, RepairPlan, TechStackFingerprint
from gamedoctor.textfile import TextDoc, detect_encoding

FP = TechStackFingerprint(game_name="test_game")


def _executor(tmp_path: Path, mode: ExecutionMode = ExecutionMode.AUTO) -> TransactionalExecutor:
    """构造一个备份落在 tmp 目录、默认放行确认的执行器。"""
    return TransactionalExecutor(
        backup_manager=BackupManager(backup_root=tmp_path / "backups"),
        confirm=lambda _action: True,
    )


def _replace_action(path: Path, old: str, new: str, **extra) -> RepairAction:
    params = {"path": str(path), "old": old, "new": new}
    params.update(extra)
    return RepairAction(
        id="a1",
        primitive="replace_text",
        params=params,
        level=ActionLevel.L1_SAFE,
        description="替换文本",
    )


# --------------------------------------------------------------------------- #
# 底层：替换
# --------------------------------------------------------------------------- #


def test_replace_literal_all_and_count():
    text = "a=1\na=1\na=1\n"
    new, hits = replace_literal(text, "a=1", "a=2")
    # 默认全部替换
    assert hits == 3 and new == "a=2\na=2\na=2\n"

    new, hits = replace_literal(text, "a=1", "a=2", count=1)
    # 限制次数后只改第一处
    assert hits == 1 and new == "a=2\na=1\na=1\n"


def test_replace_expect_mismatch_refuses():
    # 期望 1 处、实际 2 处 → 拒绝改写（防止改错文件或锚点漂移）
    try:
        replace_literal("x=1\nx=1\n", "x=1", "x=2", expect=1)
    except TextEditError as exc:
        assert "2 次" in str(exc)
    else:  # pragma: no cover - 未抛异常即测试失败
        raise AssertionError("期望不符时应抛 TextEditError")


def test_replace_regex_with_backreference():
    new, hits = replace_regex("fps = 240\n", r"fps\s*=\s*\d+", "fps = 60")
    assert hits == 1 and new == "fps = 60\n"


# --------------------------------------------------------------------------- #
# 底层：结构化改值
# --------------------------------------------------------------------------- #


def test_set_ini_value_preserves_comments_and_layout():
    text = "; 我的自定义配置\n[Graphics]\nResolution=1920x1080\n; 下一项是帧率上限\nFPS=240\n"
    new, hits = set_ini_value(text, "FPS", "60", section="Graphics")
    assert hits == 1
    # 注释与其它键必须原样保留（configparser 做不到这一点）
    assert new.startswith("; 我的自定义配置\n")
    assert "Resolution=1920x1080" in new and "; 下一项是帧率上限" in new
    assert "FPS=60" in new and "FPS=240" not in new


def test_set_ini_value_appends_missing_section():
    new, hits = set_ini_value("[A]\nx=1\n", "y", "2", section="B")
    assert hits == 1
    assert new.endswith("[B]\ny=2")


def test_set_json_value_nested_pointer():
    data = {"graphics": {"fps": 240}, "other": 1}
    new, _ = set_json_value(json.dumps(data, ensure_ascii=False), "graphics.fps", 60)
    # 点分路径命中嵌套键，其余字段不受影响
    assert json.loads(new) == {"graphics": {"fps": 60}, "other": 1}


# --------------------------------------------------------------------------- #
# 原语：replace_text
# --------------------------------------------------------------------------- #


def test_replace_text_applies_and_is_idempotent(tmp_path):
    target = tmp_path / "settings.cfg"
    target.write_text("maxFps=260\nvolume=1.0\n", encoding="utf-8")

    primitive = get_primitive("replace_text")
    result = primitive.apply({"path": str(target), "old": "maxFps=260", "new": "maxFps=60"}, FP)
    assert result.status.value == "success"
    assert target.read_text(encoding="utf-8") == "maxFps=60\nvolume=1.0\n"

    # 再次执行：目标态已达，应判定成功且不产生重复副作用
    again = primitive.apply({"path": str(target), "old": "maxFps=260", "new": "maxFps=60"}, FP)
    assert again.status.value == "success"
    assert target.read_text(encoding="utf-8") == "maxFps=60\nvolume=1.0\n"


def test_replace_text_missing_anchor_does_not_touch_file(tmp_path):
    target = tmp_path / "options.txt"
    original = "renderDistance:12\n"
    target.write_text(original, encoding="utf-8")

    primitive = get_primitive("replace_text")
    result = primitive.apply({"path": str(target), "old": "不存在的锚点", "new": "x"}, FP)
    # 锚点未命中：判定失败，且文件一个字节都没动
    assert result.status.value == "failed"
    assert target.read_text(encoding="utf-8") == original


def test_replace_text_preserves_utf16_bom_and_crlf(tmp_path):
    target = tmp_path / "Input.ini"
    original = "[/Script/Engine.InputSettings]\nbEnableMouseSmoothing=True\r\n"
    target.write_bytes(original.encode("utf-16"))

    primitive = get_primitive("replace_text")
    result = primitive.apply(
        {"path": str(target), "old": "bEnableMouseSmoothing=True",
         "new": "bEnableMouseSmoothing=False"},
        FP,
    )
    assert result.status.value == "success"
    raw = target.read_bytes()
    # BOM 必须还在（否则游戏读不出配置），且行尾仍为 CRLF
    assert raw.startswith(b"\xff\xfe")
    assert raw.decode("utf-16") == (
        "[/Script/Engine.InputSettings]\r\nbEnableMouseSmoothing=False\r\n"
    )


# --------------------------------------------------------------------------- #
# 原语：edit_config（ini / json / kv）
# --------------------------------------------------------------------------- #


def test_edit_config_ini(tmp_path):
    target = tmp_path / "Engine.ini"
    target.write_text("[SystemSettings]\nr.ShadowQuality=3\n", encoding="utf-8")

    primitive = get_primitive("edit_config")
    result = primitive.apply(
        {"path": str(target), "key": "r.ShadowQuality", "value": "0",
         "section": "SystemSettings"},
        FP,
    )
    assert result.status.value == "success"
    assert target.read_text(encoding="utf-8") == "[SystemSettings]\nr.ShadowQuality=0\n"
    # verify 检查点应读回新值
    assert primitive.verify(
        {"path": str(target), "key": "r.ShadowQuality", "value": "0",
         "section": "SystemSettings"}, FP
    )


def test_edit_config_json_nested(tmp_path):
    target = tmp_path / "config.json"
    target.write_text(json.dumps({"graphics": {"fps": 240}}), encoding="utf-8")

    primitive = get_primitive("edit_config")
    result = primitive.apply(
        {"path": str(target), "key": "graphics.fps", "value": 60}, FP
    )
    assert result.status.value == "success"
    assert json.loads(target.read_text(encoding="utf-8"))["graphics"]["fps"] == 60


def test_edit_config_kv_colon_separator(tmp_path):
    # Minecraft options.txt 用冒号分隔
    target = tmp_path / "options.txt"
    target.write_text("gamma:1.0\nmaxFps:260\n", encoding="utf-8")

    primitive = get_primitive("edit_config")
    result = primitive.apply({"path": str(target), "key": "maxFps", "value": 60}, FP)
    assert result.status.value == "success"
    content = target.read_text(encoding="utf-8")
    assert "maxFps:60" in content and "gamma:1.0" in content


def test_edit_config_missing_file_fails(tmp_path):
    primitive = get_primitive("edit_config")
    result = primitive.apply(
        {"path": str(tmp_path / "nope.ini"), "key": "a", "value": "1"}, FP
    )
    assert result.status.value == "failed"


# --------------------------------------------------------------------------- #
# 执行器：dry-run 预览 / 验证失败自动回滚
# --------------------------------------------------------------------------- #


def test_dry_run_renders_diff_without_touching_file(tmp_path):
    target = tmp_path / "settings.cfg"
    original = "maxFps=260\n"
    target.write_text(original, encoding="utf-8")

    plan = RepairPlan(diagnosis_id="d1", actions=[_replace_action(target, "maxFps=260",
                                                                  "maxFps=60")])
    report = _executor(tmp_path).execute(plan, FP, ExecutionMode.DRY_RUN)

    # dry-run 只输出将发生的 diff，绝不落盘
    assert report.results[0].message.startswith("[dry-run]")
    assert "+maxFps=60" in report.results[0].message
    assert target.read_text(encoding="utf-8") == original


def test_verify_failure_triggers_rollback(tmp_path):
    target = tmp_path / "settings.cfg"
    original = "maxFps=260\nkeep=yes\n"
    target.write_text(original, encoding="utf-8")

    # absent 指定"改完后不应再出现 keep=yes"，而替换并不会去掉它 → 验证必然失败
    action = _replace_action(target, "maxFps=260", "maxFps=60", absent="keep=yes")
    plan = RepairPlan(diagnosis_id="d2", actions=[action])
    report = _executor(tmp_path).execute(plan, FP, ExecutionMode.AUTO)

    assert report.results[0].status.value == "rolled_back"
    # 验证未通过 → 备份还原，文件内容必须回到原样
    assert target.read_text(encoding="utf-8") == original


def test_replace_text_through_executor_with_backup(tmp_path):
    target = tmp_path / "settings.cfg"
    target.write_text("maxFps=260\n", encoding="utf-8")

    plan = RepairPlan(diagnosis_id="d3", actions=[_replace_action(target, "maxFps=260",
                                                                  "maxFps=60")])
    report = _executor(tmp_path).execute(plan, FP, ExecutionMode.AUTO)

    assert report.succeeded == 1 and report.failed == 0
    assert target.read_text(encoding="utf-8") == "maxFps=60\n"
    # 备份应已生成，供 rollback 命令一键还原
    backups = BackupManager(backup_root=tmp_path / "backups").list_records("d3")
    assert len(backups) == 1


def test_text_doc_roundtrip_keeps_trailing_newline_state(tmp_path):
    target = tmp_path / "no_trailing.txt"
    target.write_text("a=1", encoding="utf-8")  # 末尾无换行

    doc = TextDoc.load(target)
    doc.write(doc.text.replace("a=1", "a=2"))
    # 原本没有末尾换行，写回后也不应凭空多出一个
    assert target.read_text(encoding="utf-8") == "a=2"


# --------------------------------------------------------------------------- #
# 「读」的一半：编辑前先看原文
# --------------------------------------------------------------------------- #


def test_read_preview_reports_encoding_and_truncates(tmp_path):
    target = tmp_path / "GameUserSettings.ini"
    # 用 write_bytes 而非 write_text：后者在 Windows 上会把 \n 翻译成 \r\n
    target.write_bytes(("[A]\nx=" + "1" * 500 + "\n").encode("utf-8"))

    preview = peek_text_file(target, max_chars=64)
    # 预览要带上元信息，让上层知道文件编码与是否被截断
    assert "编码: utf-8" in preview and "行尾: LF" in preview
    assert "已截断" in preview


def test_detect_encoding_gbk_and_utf16(tmp_path):
    target = tmp_path / "说明.txt"
    target.write_text("画质设置=高\n", encoding="gbk")
    assert detect_encoding(target) == "gbk"

    target.write_bytes("bEnable=True\r\n".encode("utf-16"))
    # UTF-16 带 BOM，应被识别为 utf-16 而非 utf-8
    assert detect_encoding(target) == "utf-16"
