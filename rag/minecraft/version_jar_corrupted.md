---
title: "游戏版本文件损坏（version json / jar 损坏）"
game: "Minecraft"
mod_name: NULL
category: "文件完整性问题"
tags: ["version", "jar", "corruption", "download", "闪退", "修复"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

版本 jar 文件或 version.json 下载不完整或损坏，导致游戏无法启动。

# 症状表现

启动时立即崩溃，日志报 ClassNotFound、jar 文件校验失败或资产索引错误。

# 根因分析

下载中断、磁盘写入异常或杀软篡改导致版本核心文件不完整。

# 解决方案

1.使用启动器的"重新安装/修复版本"功能重新下载该版本
2.手动删除 `.minecraft\versions\<游戏版本>` 整个文件夹后重新安装
3.若反复损坏，检查磁盘健康状态和杀毒软件设置

# 相关文件路径

- `.minecraft\versions\<游戏版本>\<游戏版本>.jar`
- `.minecraft\versions\<游戏版本>\<游戏版本>.json`
- `.minecraft\assets\`

# 常见错误日志

- `java.lang.ClassNotFoundException`
- `Error trying to download asset`
- `Failed to verify authentication`
- `java.util.zip.ZipException: error in opening zip file`
