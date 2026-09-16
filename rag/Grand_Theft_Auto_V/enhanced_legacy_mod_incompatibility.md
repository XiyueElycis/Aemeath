---
title: "Enhanced 版与 Legacy 版 Mod 不兼容"
game: "Grand Theft Auto V"
mod_name: NULL
category: "版本兼容问题"
tags: ["steam", "enhanced", "legacy", "compatibility", "mod", "版本"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

将为 Legacy 版制作的 Mod 安装到 Enhanced 版（或反之），导致 Mod 不生效、贴图错误、脚本崩溃或游戏启动失败。

# 症状表现

Mod 安装后无任何效果；进入游戏出现贴图错乱、花屏；加载脚本时崩溃；部分情况启动即闪退。

# 根因分析

Enhanced 版于 2025 年发布，升级了图形管线、文件结构与加密方式，与 Legacy 版存在架构差异。Legacy 版的 Mod 依赖的函数偏移、资源格式在 Enhanced 版中已改变，直接复用会失效或引发崩溃。两个版本需要各自对应的 Mod 版本。

# 解决方案

1.确认当前游戏版本：Enhanced 版还是 Legacy 版（可在启动器或游戏属性中查看）
2.下载 Mod 时选择与版本对应的文件，注意发布页标注的 Enhanced / Legacy 支持说明
3.图形类、脚本类、替换类 Mod 都需分别确认版本兼容性，不能默认通用
4.若 Mod 作者尚未发布 Enhanced 版适配，只能等待更新或暂时回退到 Legacy 版

# 相关文件路径

- `Steam\steamapps\common\Grand Theft Auto V\`（游戏根目录）

# 常见错误日志

- `The game crashed whilst initializing game`
- `ScriptHookV.dll: unsupported game version`
- 启动闪退且无明确日志
