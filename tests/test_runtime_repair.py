"""运行库检测与「仅指引」修复原语测试。

覆盖：
- VC++ 注册表显示名解析 / dotnet --list-runtimes 行解析（纯函数）；
- 运行库目录完整性（官方链接、静默参数、id 对齐）；
- 依据运行时指纹的组件安装状态判定；
- component 自然语言别名归一化；
- install_runtime 固定 L3：策略层兜底拦截、执行器只给指引永不执行、
  dry-run 同样渲染指引。
"""

from __future__ import annotations

from pathlib import Path

import pytest

# 导入原语包以填充 REGISTRY（policy 兜底拦截依赖它）
import gamedoctor.fixer.primitives  # noqa: F401
from gamedoctor.fingerprint.runtime import (
    parse_dotnet_runtime_line,
    parse_vc_redist_name,
)
from gamedoctor.fixer.backup.manager import BackupManager
from gamedoctor.fixer.executor.transactional import ExecutionMode, TransactionalExecutor
from gamedoctor.fixer.policy.levels import DefaultAuthorizationPolicy
from gamedoctor.fixer.primitives import get as get_primitive
from gamedoctor.fixer.primitives.runtime import (
    SUPPORTED_RUNTIME_COMPONENTS,
    build_guidance,
    component_installed,
    resolve_component,
)
from gamedoctor.models import (
    ActionLevel,
    FixStatus,
    RepairAction,
    RepairPlan,
    RuntimeFingerprint,
    TechStackFingerprint,
)


# --------------------------------------------------------------------- #
# 注册表显示名解析
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("display_name,expected", [
    ("Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.44.35207",
     ("vc2015-2022", "x64", "14.44.35207")),
    ("Microsoft Visual C++ 2022  Redistributable (x86) - 14.30.30704",
     ("vc2015-2022", "x86", "14.30.30704")),
    ("Microsoft Visual C++ 2013  x64 Minimum Runtime - 12.0.40664",
     ("vc2013", "x64", "12.0.40664")),
    ("Microsoft Visual C++ 2013  x64 Additional Runtime - 12.0.40664",
     ("vc2013", "x64", "12.0.40664")),
    ("Microsoft Visual C++ 2010  x64 Redistributable - 10.0.40219",
     ("vc2010", "x64", "10.0.40219")),
    ("Microsoft Visual C++ 2008 Redistributable - x64 9.0.30729.6161",
     ("vc2008", "x64", "9.0.30729.6161")),
    # 14.x 工具集注册名不带年份（真实机器采样）
    ("Microsoft Visual C++ v14 Redistributable (x64) - 14.51.36247",
     ("vc2015-2022", "x64", "14.51.36247")),
    ("Microsoft Visual C++ v14 Redistributable (x86) - 14.51.36247",
     ("vc2015-2022", "x86", "14.51.36247")),
])
def test_parse_vc_redist_name(display_name, expected):
    parsed = parse_vc_redist_name(display_name)
    assert parsed is not None
    assert (parsed["family"], parsed["arch"], parsed["version"]) == expected
    assert parsed["id"] == f"{expected[0]}_{expected[1]}"


def test_parse_vc_redist_name_legacy_x86_default_and_non_vc():
    # 早期安装包名称不带架构 → 默认 x86
    parsed = parse_vc_redist_name(
        "Microsoft Visual C++ 2005 Redistributable - 6.0.2900.2180")
    assert parsed is not None and parsed["arch"] == "x86"
    # 非 VC++ 名称
    assert parse_vc_redist_name("Google Chrome 120.0") is None
    assert parse_vc_redist_name("") is None


def test_parse_dotnet_runtime_line():
    assert parse_dotnet_runtime_line(
        "Microsoft.WindowsDesktop.App 8.0.4 [C:\\Program Files\\dotnet]"
    ) == "WindowsDesktop 8.0.4"
    assert parse_dotnet_runtime_line(
        "Microsoft.NETCore.App 6.0.25 [/usr/share/dotnet]"
    ) == "NETCore 6.0.25"
    # ASP.NET Core 与垃圾行不参与游戏桌面运行时判定
    assert parse_dotnet_runtime_line(
        "Microsoft.AspNetCore.App 8.0.4 [path]") is None
    assert parse_dotnet_runtime_line("") is None


# --------------------------------------------------------------------- #
# 运行库目录完整性
# --------------------------------------------------------------------- #
def test_catalog_integrity():
    assert len(SUPPORTED_RUNTIME_COMPONENTS) >= 10
    for cid, comp in SUPPORTED_RUNTIME_COMPONENTS.items():
        assert comp.id == cid
        assert comp.download_url.startswith("https://")
        assert comp.installer and comp.installer.strip()
        assert comp.quiet_args and "/" in comp.quiet_args
        assert comp.display
        # 链接只允许微软官方域名
        host = comp.download_url.split("/")[2].lower()
        assert host.endswith("microsoft.com") or host == "aka.ms", host


def test_catalog_covers_key_components():
    for cid in ("vc2015-2022_x64", "vc2015-2022_x86", "vc2013_x64", "vc2012_x86",
                "vc2010_x86", "dotnetfx48", "dotnet_desktop_8_x64",
                "dotnet_desktop_9_x64", "directx_june2010"):
        assert cid in SUPPORTED_RUNTIME_COMPONENTS


# --------------------------------------------------------------------- #
# 状态判定
# --------------------------------------------------------------------- #
def _fp(**kw) -> TechStackFingerprint:
    return TechStackFingerprint(game_name="G", runtime=RuntimeFingerprint(**kw))


def test_component_installed_vc():
    fp = _fp(vc_components=["vc2015-2022_x64", "vc2013_x86"])
    ok, evidence = component_installed("vc2015-2022_x64", fp.runtime)
    assert ok is True and "已包含" in evidence
    ok, _ = component_installed("vc2015-2022_x86", fp.runtime)
    assert ok is False
    ok, _ = component_installed("vc2013_x86", fp.runtime)
    assert ok is True


def test_component_installed_dotnet():
    ok, evidence = component_installed("dotnetfx48", _fp(dotnet="4.8").runtime)
    assert ok is True and "4.8" in evidence
    ok, _ = component_installed("dotnetfx48", _fp(dotnet="4.7.2").runtime)
    assert ok is False
    ok, _ = component_installed("dotnetfx48", _fp().runtime)
    assert ok is None  # 未探测 → 未知，不冤枉用户


def test_component_installed_dotnet_core_and_dx():
    fp = _fp(dotnet_runtimes=["WindowsDesktop 8.0.4", "NETCore 6.0.25"],
             directx_legacy=False)
    ok, _ = component_installed("dotnet_desktop_8_x64", fp.runtime)
    assert ok is True
    ok, _ = component_installed("dotnet_desktop_9_x64", fp.runtime)
    assert ok is False
    ok, evidence = component_installed("directx_june2010", fp.runtime)
    assert ok is False and "d3dx9_43" in evidence

    fp2 = _fp(directx_legacy=None)
    ok, _ = component_installed("directx_june2010", fp2.runtime)
    assert ok is None


# --------------------------------------------------------------------- #
# 别名归一化
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("vc2015-2022_x64", "vc2015-2022_x64"),
    ("VC++ 2015-2022 x64", "vc2015-2022_x64"),
    ("Visual C++ 2013 Redistributable 64位", "vc2013_x64"),
    ("vcredist 2012 32位", "vc2012_x86"),
    ("VC++ 2010", "vc2010_x86"),              # 老库无架构线索 → x86
    ("Visual C++ v14 x64", "vc2015-2022_x64"),  # 工具集代称 v14
    ("缺 Visual C++ 运行库", "vc2015-2022_x64"),  # 无年份 → 现役版本 x64
    (".NET Framework 4.8", "dotnetfx48"),
    ("dotnet 8 desktop runtime", "dotnet_desktop_8_x64"),
    (".NET 9.0 Desktop Runtime x64", "dotnet_desktop_9_x64"),
    ("directx 9", "directx_june2010"),
    ("缺少 d3dx9_43.dll", "directx_june2010"),
    ("xinput1_3.dll 丢失", "directx_june2010"),
])
def test_resolve_component_aliases(raw, expected):
    assert resolve_component(raw) == expected


def test_resolve_component_unknown():
    assert resolve_component("") is None
    assert resolve_component("photoshop") is None
    assert resolve_component("vc++ arm64") is None  # 目录无 arm64 安装包


# --------------------------------------------------------------------- #
# 指引内容
# --------------------------------------------------------------------- #
def test_guidance_content_missing():
    fp = _fp(vc_components=[])
    text = build_guidance("vc2015-2022_x64", fp)
    assert "未安装" in text
    assert "https://aka.ms/vs/17/release/vc_redist.x64.exe" in text
    assert "/install /quiet /norestart" in text
    assert "管理员" in text
    assert "不可由本工具备份回滚" in text


def test_guidance_content_installed_and_unknown():
    fp = _fp(vc_components=["vc2015-2022_x64"], directx_legacy=True)
    assert "已安装" in build_guidance("vc2015-2022_x64", fp)
    unknown = build_guidance("", fp)
    assert "支持的 component" in unknown
    assert "vc2015-2022_x64" in unknown  # 列出全部可选 id


# --------------------------------------------------------------------- #
# 原语分级与执行器端到端
# --------------------------------------------------------------------- #
def test_install_runtime_is_l3_and_registered():
    prim = get_primitive("install_runtime")
    assert prim.default_level == ActionLevel.L3_FORBIDDEN
    assert prim.affected_paths({"component": "vc2015-2022_x64"}, _fp()) == []


def test_policy_forces_l3_even_when_plan_says_l2():
    """LLM 把运行库安装标成 L2 也必须被策略层强制打回 L3。"""
    policy = DefaultAuthorizationPolicy()
    action = RepairAction(
        id="a1", primitive="install_runtime",
        params={"component": "vc2015-2022_x64"},
        level=ActionLevel.L2_CONFIRM, description="安装 VC++ 运行库",
    )
    assert policy.classify(action) == ActionLevel.L3_FORBIDDEN
    assert not policy.can_auto(ActionLevel.L3_FORBIDDEN)


def _runtime_action(level=ActionLevel.L2_CONFIRM, component="vc2015-2022_x64"):
    return RepairAction(
        id="a1", primitive="install_runtime", params={"component": component},
        level=level, description="安装 VC++ 2015-2022 x64 运行库",
    )


def test_executor_l3_never_installs_but_gives_guidance(tmp_path: Path):
    fp = _fp(vc_components=[])
    plan = RepairPlan(diagnosis_id="d-runtime", actions=[_runtime_action()])
    executor = TransactionalExecutor(
        backup_manager=BackupManager(backup_root=tmp_path / "backups"),
        confirm=lambda _a: pytest.fail("L3 不应进入确认环节"),
    )
    report = executor.execute(plan, fp, ExecutionMode.AUTO)

    assert len(report.results) == 1
    result = report.results[0]
    assert result.status == FixStatus.NEEDS_MANUAL
    assert result.verified is None
    assert "vc_redist.x64.exe" in result.message
    assert "未安装" in result.message
    # 系统级动作没有任何备份产物
    assert not (tmp_path / "backups").exists() or not any(
        (tmp_path / "backups").rglob("*"))


def test_executor_dry_run_shows_guidance(tmp_path: Path):
    fp = _fp(vc_components=["vc2015-2022_x64"])
    plan = RepairPlan(diagnosis_id="d-runtime2", actions=[_runtime_action()])
    executor = TransactionalExecutor(
        backup_manager=BackupManager(backup_root=tmp_path / "backups"))
    report = executor.execute(plan, fp, ExecutionMode.DRY_RUN)
    msg = report.results[0].message
    assert "[dry-run]" in msg
    assert "已安装" in msg  # dry-run 同样带真实检测状态
