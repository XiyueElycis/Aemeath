---
title: "显卡驱动过旧导致渲染崩溃"
game: "Minecraft"
mod_name: NULL
category: "图形渲染问题"
tags: ["opengl", "driver", "gpu", "graphics", "闪退", "渲染"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

显卡驱动版本过旧、损坏或不兼容，导致游戏初始化渲染时崩溃。

# 症状表现

启动时黑屏后崩溃，崩溃报告指向 OpenGL 初始化或着色器编译失败；核显 + 独显的笔记本常误用核显运行。

# 根因分析

Minecraft 依赖 OpenGL 渲染，驱动过旧会缺少游戏需要的 OpenGL 特性或存在已知缺陷。部分品牌机的默认驱动是系统自带的基础驱动，缺少完整 OpenGL 支持。

# 解决方案

1.到 NVIDIA / AMD / Intel 官网或对应品牌支持页下载最新显卡驱动并安装
2.笔记本用户在显卡控制面板中把 javaw.exe 指定为高性能显卡运行
3.安装后重启电脑再启动游戏

# 相关文件路径

- `.minecraft\crash-reports\crash-*.txt`

# 常见错误日志

- `OpenGL rendering seems to be disabled`
- `Failed to create window`
- `Couldn't set pixel format`
- `java.lang.IllegalStateException: GLFW error`
