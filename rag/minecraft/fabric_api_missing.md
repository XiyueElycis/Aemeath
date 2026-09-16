---
title: "缺少 Fabric API"
game: "Minecraft"
mod_name: "Fabric API"
category: "Mod 加载问题"
tags: ["fabric", "fabric-api", "dependency", "mod", "前置"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

Fabric 端绝大多数 Mod 依赖 Fabric API，未安装时游戏无法启动。

# 症状表现

启动时弹出缺失 Mod 的提示窗口，或日志报缺少 fabric-api，游戏停在加载界面。

# 根因分析

Fabric Loader 只是加载器本体，Fabric API 是一个独立的库 Mod，需要单独下载安装。

# 解决方案

1.到 Modrinth 或 CurseForge 搜索 Fabric API
2.下载与游戏版本一致的 fabric-api 文件放入 mods 目录
3.重新启动游戏

# 相关文件路径

- `.minecraft\mods`

# 常见错误日志

- `Could not find required mod: fabric-api`
- `Missing required mod: Fabric API`
- `requires the mod Fabric API`
