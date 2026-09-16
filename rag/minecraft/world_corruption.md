---
title: "存档损坏（Level 加载失败）"
game: "Minecraft"
mod_name: NULL
category: "存档问题"
tags: ["world", "save", "corruption", "level", "崩溃", "存档"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

进入某个存档时崩溃或提示无法加载，通常因区块数据损坏、突然断电或 Mod 残留数据导致。

# 症状表现

点击存档进入时卡住或崩溃，崩溃报告显示 level 加载异常；有时进入世界即闪退。

# 根因分析

区块文件（region 文件）损坏、断电或强制关机导致写入中断、已卸载的 Mod 留下了残留方块或实体数据。

# 解决方案

1.进入存档前先备份整个存档文件夹
2.用 Region Fixer 或 MCA Selector 等工具修复损坏的区块（删除损坏区块会丢失该区域建筑）
3.若是 Mod 残留导致，临时装回该 Mod 进入存档清理后再卸载
4.使用存档自带的 level.dat_old 恢复（将 level.dat_old 重命名为 level.dat）

# 相关文件路径

- `.minecraft\saves\<存档名>\`
- `.minecraft\saves\<存档名>\region\`
- `.minecraft\crash-reports\crash-*.txt`

# 常见错误日志

- `java.lang.NullPointerException: Loading entity`
- `Failed to load level`
- `The game crashed whilst ticking entity`
- `Exception reading level.dat`
