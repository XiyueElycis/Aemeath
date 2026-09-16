---
title: "Mixin 注入冲突导致崩溃"
game: "Minecraft"
mod_name: NULL
category: "Mod 加载问题"
tags: ["mixin", "crash", "mod", "冲突", "transformer"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

两个或多个 Mod 对同一处游戏代码进行 Mixin 注入时发生冲突，或某 Mod 的 Mixin 与当前游戏版本不兼容，导致启动崩溃。

# 症状表现

启动加载到一半崩溃，崩溃报告指向 Mixin 应用失败，通常带有冲突双方的 Mod 名或类名。

# 根因分析

多个 Mod 修改了同一个方法或类，或 Mod 版本过旧不适配当前游戏版本，导致字节码注入失败。

# 解决方案

1.查看崩溃报告定位冲突涉及的 Mod 名称
2.更新冲突双方的 Mod 到最新版本
3.若仍冲突，移除其中一个或寻找兼容补丁（查看 Mod 说明页的 compatibility 说明）
4.二分排查：将 mods 目录分半移除，逐步定位问题 Mod

# 相关文件路径

- `.minecraft\crash-reports\crash-*.txt`
- `.minecraft\versions\<游戏版本>\logs\latest.log`

# 常见错误日志

- `Mixin apply failed`
- `MixinTransformerError: An critical failure occurred`
- `org.spongepowered.asm.mixin.throwables.MixinApplyError`
- `@Mixin target was not found`
