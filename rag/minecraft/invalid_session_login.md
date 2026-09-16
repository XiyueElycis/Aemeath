---
title: "登录验证失败（Invalid Session）"
game: "Minecraft"
mod_name: NULL
category: "账号登录问题"
tags: ["login", "session", "microsoft", "authentication", "离线", "验证"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

启动器登录态过期或验证服务异常，导致无法进入游戏或联机时提示无效会话。

# 症状表现

启动器提示登录失败；联机时提示"无效的会话"；进入服务器提示 Failed to verify username。

# 根因分析

会话令牌过期（账号在其他设备登录会挤掉当前会话）、网络到验证服务不通、系统时间不准导致证书校验失败。

# 解决方案

1.在启动器中退出账号后重新登录
2.校准系统时间（设置 → 时间和语言 → 自动设置时间）
3.检查网络，必要时切换网络环境后重试
4.服务器端提示 Failed to verify username 时，可让服主在 server.properties 中将 online-mode 设为 false（仅限离线服）

# 相关文件路径

- `.minecraft\launcher_profiles.json`
- 服务器端 `server.properties`

# 常见错误日志

- `Invalid session (Try restarting your game)`
- `Failed to verify username`
- `Authentication servers are down`
