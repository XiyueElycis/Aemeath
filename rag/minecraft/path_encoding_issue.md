---
title: "游戏目录路径含中文或特殊字符导致异常"
game: "Minecraft"
mod_name: NULL
category: "文件完整性问题"
tags: ["path", "encoding", "中文路径", "乱码", "崩溃"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

游戏安装目录、用户目录或 Java 路径中包含中文、空格或特殊字符，导致部分功能异常或崩溃。

# 症状表现

启动报路径相关错误、文件读取失败、控制台乱码；部分 Mod 无法读取配置文件；日志显示 FileNotFoundException。

# 根因分析

部分组件（尤其第三方 Mod 和原生库）对非 ASCII 路径支持不佳；用户名含中文时 `%APPDATA%\.minecraft` 路径本身即含中文。

# 解决方案

1.将游戏目录迁移到纯英文、无空格路径（如 `D:\games\.minecraft`）
2.在启动器中重新指定新的游戏目录
3.用户名含中文时，在启动器设置中将游戏目录指向其他盘符的英文路径

# 相关文件路径

- `.minecraft\config`
- 启动器设置中的游戏目录配置项

# 常见错误日志

- `java.io.FileNotFoundException`
- `The system cannot find the path specified`
- `java.nio.file.InvalidPathException`
