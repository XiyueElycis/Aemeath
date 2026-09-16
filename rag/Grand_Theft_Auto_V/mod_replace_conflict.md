---
title: "多个替换类 Mod 互相冲突"
game: "Grand Theft Auto V"
mod_name: NULL
category: "Mod 冲突问题"
tags: ["steam", "enhanced", "legacy", "conflict", "replace", "冲突", "mod"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

同时安装多个替换同一个游戏资源的 Mod（如替换同一辆车、同一张贴图），导致资源错乱或游戏崩溃。

# 症状表现

游戏内出现贴图错误、模型缺失、车辆消失；进入特定区域或触发特定内容时崩溃；游戏启动后黑屏卡死。

# 根因分析

多个替换类（replace）Mod 覆盖了同一归档内的同一文件，后安装的覆盖先安装的，或被部分覆盖后文件结构损坏。未使用 OpenIV 的 mods 文件夹进行隔离管理时，冲突直接发生在原始游戏文件上，难以回退。

# 解决方案

1.冲突发生时，移除最近安装的替换类 Mod，逐个排查确认冲突源
2.启用 OpenIV 的"Mod 文件夹"模式，将替换类 Mod 安装到 `mods` 文件夹中，而非直接覆盖原始文件
3.同一资源只保留一个替换类 Mod，卸载冲突方
4.若原始文件已被覆盖损坏，通过 Steam 验证游戏文件完整性恢复（见对应条目）

# 相关文件路径

- `Steam\steamapps\common\Grand Theft Auto V\mods\`
- `Steam\steamapps\common\Grand Theft Auto V\update\update.rpf\`

# 常见错误日志

- 无明确报错，表现为贴图/模型异常
- 进入特定内容时崩溃且崩溃位置固定
