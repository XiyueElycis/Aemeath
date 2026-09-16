---
title: "ScriptHookV 与游戏版本不匹配"
game: "Grand Theft Auto V"
mod_name: "ScriptHookV"
category: "Mod 加载问题"
tags: ["steam", "enhanced", "legacy", "ScriptHookV", "version", "mod"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

游戏更新后或安装 Mod 后启动游戏，弹出 ScriptHookV 版本不支持的提示窗口，Mod 无法加载，部分情况游戏直接无法进入。

# 症状表现

启动时弹出对话框提示 "Unsupported game version" 或 ScriptHookV 需要更新；按提示退出后游戏无法正常运行；部分脚本类 Mod 全部失效。

# 根因分析

ScriptHookV 是针对特定游戏版本编译的注入框架。GTA5 更新（尤其是 DLC 更新）会改变游戏内部版本号和函数偏移，旧版 ScriptHookV 无法识别新版本。Enhanced 版与 Legacy 版需要各自对应的 ScriptHookV 构建版本，两者不能混用。

# 解决方案

1.确认游戏是 Enhanced 还是 Legacy 版，到 ScriptHookV 官方发布页下载对应最新版本
2.解压后将 `ScriptHookV.dll` 与 `dinput8.dll`（或配套的 ASI Loader）复制到游戏根目录覆盖旧文件
3.若官方尚未发布适配当前游戏版本的 ScriptHookV，暂时无法使用脚本类 Mod，需等待作者更新或在 Steam 中暂缓游戏更新
4.安装后启动游戏，查看根目录的 `ScriptHookV.log` 确认加载成功

# 相关文件路径

- `Steam\steamapps\common\Grand Theft Auto V\ScriptHookV.dll`
- `Steam\steamapps\common\Grand Theft Auto V\dinput8.dll`
- `Steam\steamapps\common\Grand Theft Auto V\ScriptHookV.log`

# 常见错误日志

- `Unsupported game version`（弹窗提示）
- `ScriptHookV.dll: unsupported game version`
- `This version of ScriptHookV is not compatible`
