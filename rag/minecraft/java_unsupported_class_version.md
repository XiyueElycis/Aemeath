---
title: "Java 版本与游戏版本不匹配"
game: "Minecraft"
mod_name: NULL
category: "Java 运行环境问题"
tags: ["java", "UnsupportedClassVersionError", "launcher", "crash", "闪退"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

启动游戏时因 Java 版本过低（或个别情况下过高）导致无法启动，抛出 UnsupportedClassVersionError 异常。

# 症状表现

点击启动后游戏闪退或启动器直接报错，日志中出现 class file version 相关异常。

# 根因分析

Minecraft 各版本对 Java 版本有硬性要求：1.12.2 及以下需要 Java 8；1.17 需要 Java 16；1.18～1.20.4 需要 Java 17；1.20.5 及以上需要 Java 21。启动器配置的 Java 版本低于游戏要求时即报错。报错中的 class file version 与 Java 版本对应关系：52=Java 8、60=Java 16、61=Java 17、65=Java 21。

# 解决方案

1.根据游戏版本安装对应的 Java（Java 8 / 16 / 17 / 21）
2.在启动器设置中手动指定正确的 javaw.exe 路径
3.启动器支持自动匹配 Java 时开启自动选择功能

# 相关文件路径

- `.minecraft\versions\<游戏版本>\logs\latest.log`

# 常见错误日志

- `java.lang.UnsupportedClassVersionError: net/minecraft/client/main/Main has been compiled by a more recent version of the Java Runtime (class file version 61.0), this version of the Java Runtime only recognizes class file versions up to 52.0`
- `Exception in thread "main" java.lang.ClassFormatError`
