---
title: "Java 堆内存不足（OutOfMemoryError）"
game: "Minecraft"
mod_name: NULL
category: "内存问题"
tags: ["memory", "OutOfMemoryError", "jvm", "crash", "内存不足", "卡顿"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

游戏运行或加载时 Java 堆内存耗尽，导致卡顿、闪退或崩溃。

# 症状表现

加载缓慢、长时间卡住后闪退；崩溃报告或日志中出现 OutOfMemoryError；大型整合包加载时尤为常见。

# 根因分析

启动参数分配的堆内存（-Xmx）过小，无法满足游戏加 Mod 的内存需求；32 位 Java 最多只能分配约 1.5GB 内存，天然不够用。

# 解决方案

1.在启动器中调大 JVM 参数，如 `-Xmx4G`（分配 4GB，视物理内存调整，建议给系统预留 2~4GB）
2.确认安装的是 64 位 Java
3.内存仍不足时安装优化类 Mod（如 Sodium / Embeddium）或减少 Mod 数量
4.不要过度分配内存，超出物理内存会触发频繁磁盘交换反而更卡

# 相关文件路径

- `.minecraft\crash-reports\crash-*.txt`
- `.minecraft\versions\<游戏版本>\logs\latest.log`

# 常见错误日志

- `java.lang.OutOfMemoryError: Java heap space`
- `java.lang.OutOfMemoryError: Metaspace`
- `The game crashed whilst rendering screen`
