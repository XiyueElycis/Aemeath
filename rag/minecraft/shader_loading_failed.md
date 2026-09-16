---
title: "着色器加载失败（Shader 崩溃）"
game: "Minecraft"
mod_name: NULL
category: "图形渲染问题"
tags: ["shader", "sodium", "iris", "optifine", "graphics", "崩溃"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

安装光影包（Shader）后，因光影文件损坏、与渲染 Mod 版本不匹配或显卡不支持，导致进入游戏世界时崩溃。

# 症状表现

进入存档时崩溃或贴图全黑/花屏，崩溃报告涉及着色器编译错误。

# 根因分析

光影包与渲染 Mod（OptiFine 或 Iris + Sodium）版本不匹配；光影文件下载不完整；显卡或驱动不支持该光影的 GLSL 特性。

# 解决方案

1.确认光影包与 OptiFine / Iris 的版本匹配关系（查看光影包下载页说明）
2.重新下载光影包，避免文件损坏
3.进入游戏前先在视频设置里切换为无光影，确认基础渲染正常后再逐个启用
4.更新显卡驱动

# 相关文件路径

- `.minecraft\shaderpacks`
- `.minecraft\crash-reports\crash-*.txt`

# 常见错误日志

- `Shader compilation failed`
- `Error in shader program`
- `Could not load shader`
- `net.irisshaders.batchedentityrendering.impl.ShaderCompileException`
