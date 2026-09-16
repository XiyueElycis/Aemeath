---
title: "Mod 文件放错目录导致不生效"
game: "Grand Theft Auto V"
mod_name: NULL
category: "Mod 安装问题"
tags: ["steam", "enhanced", "legacy", "install", "路径", "目录", "不生效"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

按说明安装了 Mod，但因文件放错目录或层级不对，Mod 完全不生效且无任何报错。

# 症状表现

游戏正常启动运行，但 Mod 功能不生效；脚本无反应；替换的车辆/人物没有出现。

# 根因分析

不同类型的 Mod 有各自的目标目录：脚本类（.dll/.cs）必须放 `scripts` 文件夹；.asi 加载器文件放游戏根目录；替换类资源需通过 OpenIV 放入指定 .rpf 归档路径；部分车辆包需同时注册 dlclist。常见错误包括：把脚本文件直接丢在根目录、替换文件放错 .rpf 路径、压缩包内多套了一层文件夹未展开。

# 解决方案

1.重新阅读 Mod 说明页的安装说明，确认每类文件的目标路径
2.检查是否多套了一层文件夹：解压后应直接是文件本身，而不是再包一层同名文件夹
3.脚本类文件确认在 `scripts` 文件夹内；替换类文件确认 OpenIV 中路径与说明一致
4.车辆/附加内容类检查 `dlclist.xml` 是否已添加对应条目

# 相关文件路径

- `Steam\steamapps\common\Grand Theft Auto V\scripts\`
- `Steam\steamapps\common\Grand Theft Auto V\mods\`
- `Steam\steamapps\common\Grand Theft Auto V\update\update.rpf\common\data\dlclist.xml`

# 常见错误日志

- 无报错（静默不生效）
- `ScriptHookVDotNet.log` 中无脚本加载记录
