---
title: "启动器找不到 Java 运行环境"
game: "Minecraft"
mod_name: NULL
category: "Java 运行环境问题"
tags: ["java", "launcher", "runtime", "environment", "无法启动"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

启动器无法找到可用的 Java 运行环境，游戏无法启动。

# 症状表现

启动器提示"未找到 Java"或"Unable to locate the Java runtime"，或点击启动后没有任何反应。

# 根因分析

系统未安装 Java、Java 未加入环境变量，或启动器中配置的 Java 路径失效（如 Java 被卸载、移动或被杀软隔离）。

# 解决方案

1.安装与游戏版本匹配的 Java（版本对应关系见"Java 版本与游戏版本不匹配"条目）
2.在启动器设置中手动指定 javaw.exe 的完整路径
3.安装完成后重启启动器让其重新检测，或将 Java 加入系统 PATH 环境变量

# 相关文件路径

- 启动器设置中的 Java 路径配置项

# 常见错误日志

- `Unable to locate the Java runtime`
- `No Java runtime present`
- `找不到有效的 Java 运行环境`
