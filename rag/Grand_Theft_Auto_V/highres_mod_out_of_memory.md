---
title: "加载大量高清替换类 Mod 导致内存/显存不足"
game: "Grand Theft Auto V"
mod_name: NULL
category: "性能问题"
tags: ["steam", "enhanced", "legacy", "memory", "vram", "显存", "卡顿", "崩溃"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

安装大量 4K/8K 高清贴图、高分辨率车辆等替换类 Mod 后，游戏卡顿、贴图加载缓慢或崩溃。

# 症状表现

进入城市区域严重卡顿；贴图延迟加载（糊脸）；显存占用超过上限后游戏报错或崩溃；部分情况直接闪退。

# 根因分析

高清替换类 Mod 显著增加显存与内存占用，超出硬件可用资源。Legacy 版为 32 位内存模型相关限制较少但仍有上限，Enhanced 版图形负载本身已高于 Legacy，叠加高清 Mod 后更容易爆显存。

# 解决方案

1.在游戏图形设置中查看显存占用条，降低纹理质量或减少高清 Mod 数量
2.移除部分 8K 级贴图类 Mod，保留在显存容量内的组合
3.关闭游戏内其他高负载选项（如 MSAA）为 Mod 腾出资源
4.使用 VisualSettings / gameconfig 限制类调整时需匹配当前游戏版本

# 相关文件路径

- `Steam\steamapps\common\Grand Theft Auto V\`（游戏根目录）
- `C:\Users\<用户名>\Documents\Rockstar Games\GTA V\settings.xml`

# 常见错误日志

- `ERR_MEM_EMBEDDEDCONTENT`（显存/内存不足弹窗）
- 贴图延迟加载无明确日志
- 游戏闪退无日志
