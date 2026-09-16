---
title: "ScriptHookVDotNet 脚本加载失败"
game: "Grand Theft Auto V"
mod_name: "ScriptHookVDotNet"
category: "Mod 加载问题"
tags: ["steam", "enhanced", "legacy", "ScriptHookVDotNet", "SHVDN", "dotnet", "mod"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

安装了 .cs / .dll 脚本类 Mod 后，脚本无法加载运行，功能不生效。

# 症状表现

游戏可正常启动，但脚本类 Mod（如修改器、功能脚本）没有任何反应；`ScriptHookVDotNet.log` 中记录加载错误或版本不匹配信息。

# 根因分析

脚本类 Mod 依赖 ScriptHookVDotNet（SHVDN）桥接层。不同脚本对 SHVDN 版本有要求（常见为 SHVDN3，少数旧脚本基于 SHVDN2），且 SHVDN 依赖特定 .NET Framework 运行时。SHVDN 版本、.NET 运行时与脚本要求三者任一不匹配都会导致加载失败。

# 解决方案

1.查看脚本说明页确认其要求的 SHVDN 版本（SHVDN2 还是 SHVDN3）
2.下载对应版本的 ScriptHookVDotNet，将文件放入 `scripts` 文件夹
3.确认系统已安装脚本要求的 .NET Framework / .NET 运行时
4.将脚本文件（.dll / .cs / .vb）放入 `scripts` 文件夹而非根目录
5.启动后查看 `scripts\ScriptHookVDotNet.log` 确认脚本加载成功

# 相关文件路径

- `Steam\steamapps\common\Grand Theft Auto V\scripts\`
- `Steam\steamapps\common\Grand Theft Auto V\scripts\ScriptHookVDotNet.log`
- `Steam\steamapps\common\Grand Theft Auto V\ScriptHookVDotNet.asi`

# 常见错误日志

- `Failed to load script`
- `The script is not compatible with this version of ScriptHookVDotNet`
- `System.IO.FileNotFoundException`（缺失依赖程序集）
