---
title: "联机连接失败 / 连接超时"
game: "Minecraft"
mod_name: NULL
category: "联机与网络问题"
tags: ["multiplayer", "server", "timeout", "network", "联机", "连接失败"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

多人游戏中无法连接服务器，提示连接超时或被拒绝连接。

# 症状表现

服务器列表显示无法连接；进入时长时间转圈后提示"连接超时"；或提示"连接被拒绝"。

# 根因分析

常见原因：服务器地址或端口输入错误、客户端与服务器游戏版本不一致、防火墙拦截、服务器未启动或已关闭、局域网联机未开放到局域网。

# 解决方案

1.核对服务器地址和端口（默认端口 25565），确认客户端游戏版本与服务器要求一致
2.局域网联机时，主机在游戏内选择"对局域网开放"，客户端在多人游戏列表刷新查找
3.检查防火墙和杀毒软件是否拦截了 javaw.exe
4.公网服务器确认端口映射和服务状态

# 相关文件路径

- 服务器端 `server.properties`

# 常见错误日志

- `Connection timed out: connect`
- `Connection refused`
- `Failed to connect to the server`
- `Disconnected: You are banned from this server`
