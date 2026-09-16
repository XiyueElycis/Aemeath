---
title: "OpenGL 上下文初始化失败（Failed to create window）"
game: "Minecraft"
mod_name: NULL
category: "图形渲染问题"
tags: ["opengl", "glfw", "window", "crash", "黑屏"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

游戏创建窗口或初始化 OpenGL 上下文失败，启动即崩溃。

# 症状表现

启动瞬间黑屏或直接弹出崩溃窗口，崩溃报告显示 GLFW / OpenGL 相关错误。

# 根因分析

常见原因包括：显卡驱动过旧、显卡不支持游戏所需的 OpenGL 版本（1.17+ 要求 OpenGL 3.2+）、多显示器或缩放设置异常、第三方屏幕录制软件注入干扰。

# 解决方案

1.更新显卡驱动（优先）
2.检查游戏版本对 OpenGL 的要求，确认显卡硬件支持
3.关闭屏幕录制、滤镜类覆盖层软件后重试
4.尝试关闭启动器中的高分辨率缩放或切换主显示器

# 相关文件路径

- `.minecraft\crash-reports\crash-*.txt`

# 常见错误日志

- `Failed to create window`
- `GLFW error 65543: Requested OpenGL context version 3.2, but got 1.1`
- `org.lwjgl.LWJGLException: Pixel format not accelerated`
