---
title: "安装 Mod 后进入 GTA Online 面临封禁风险"
game: "Grand Theft Auto V"
mod_name: NULL
category: "联机与反作弊问题"
tags: ["steam", "enhanced", "legacy", "online", "BattlEye", "ban", "反作弊"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

在安装单机 Mod 的状态下启动 GTA Online，触发反作弊检测，面临账号被封禁的风险。

# 症状表现

进入在线模式时游戏崩溃或被踢出；部分情况账号收到封禁通知；Enhanced 版在线模式由 BattlEye 反作弊保护，检测到注入文件可能直接拦截启动。

# 根因分析

Rockstar 官方明确规定 Mod 仅限单机（故事模式）使用，禁止在 GTA Online 中使用。Enhanced 版在线模式启用 BattlEye 反作弊，会检测并拦截注入类文件。任何脚本注入、修改器、画质注入等文件都可能被判定为作弊。

# 解决方案

1.进入 GTA Online 前移除所有注入类文件（`ScriptHookV.dll`、`dinput8.dll`、`.asi` 文件、`scripts` 文件夹等）
2.建议为联机保留一份干净的游戏文件：将原始文件备份，单机玩 Mod、联机用备份还原
3.切勿在在线模式使用任何修改器、菜单或脚本
4.确认账号数据安全：封禁可能导致线上角色与资产丢失，风险自负

# 相关文件路径

- `Steam\steamapps\common\Grand Theft Auto V\`（游戏根目录）
- 备份的干净游戏文件目录

# 常见错误日志

- `BattlEye: launch blocked`
- 在线模式启动崩溃
- 账号封禁通知
