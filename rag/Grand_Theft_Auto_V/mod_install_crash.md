---
title: "安装 Mod 后游戏启动崩溃或无限加载"
game: "Grand Theft Auto V"
mod_name: NULL
category: "Mod 崩溃问题"
tags: ["steam", "enhanced", "legacy", "crash", "闪退", "加载卡死", "mod"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

安装一个或多个 Mod 后，游戏启动即闪退、黑屏，或卡在加载界面无法进入游戏。

# 症状表现

点击启动后黑屏闪退；加载动画转圈后无响应；部分情况可进入故事模式但进入特定场景即崩溃。

# 根因分析

可能原因包括：Mod 文件损坏或下载不完整；Mod 与当前游戏版本（Enhanced/Legacy）不匹配；脚本类 Mod 的依赖缺失；Mod 之间互相冲突；Mod 版本与 ScriptHookV 版本不匹配。

# 解决方案

1.先移除最近安装的所有 Mod 文件，验证游戏能否正常启动，确认是 Mod 导致
2.若可正常启动，将移除的 Mod 逐个装回，每装一个启动一次，定位问题 Mod
3.检查问题 Mod 是否匹配当前游戏版本（Enhanced / Legacy）与 ScriptHookV 版本
4.重新下载问题 Mod，排除文件损坏
5.查看 `ScriptHookV.log`、`ScriptHookVDotNet.log` 辅助定位

# 相关文件路径

- `Steam\steamapps\common\Grand Theft Auto V\`（游戏根目录）
- `Steam\steamapps\common\Grand Theft Auto V\scripts\`
- `C:\Users\<用户名>\Documents\Rockstar Games\GTA V\`

# 常见错误日志

- `ERR_GEN_VERIFY (1)`（启动报错弹窗）
- `The game crashed whilst initializing game`
- 黑屏闪退无日志
