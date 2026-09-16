---
title: "Steam 验证文件完整性后 Mod 被清除或游戏异常"
game: "Grand Theft Auto V"
mod_name: NULL
category: "文件完整性问题"
tags: ["steam", "enhanced", "legacy", "verify", "integrity", "文件校验", "mod"]
version: "1.0"
date: "2026-09-03"
---

# 问题描述

在 Steam 中执行"验证游戏文件完整性"后，安装在原始目录中的 Mod 文件被覆盖或删除，或验证后游戏出现新的异常。

# 症状表现

验证完整性后 Mod 功能消失；验证过程中 Steam 提示大量文件需重新下载；验证完成后游戏仍报错或出现新崩溃。

# 根因分析

Steam 的文件校验会将所有与官方不一致的文件恢复为原始版本，因此直接放在游戏原始目录中的替换类文件会被覆盖。若 Mod 安装在 OpenIV 的 `mods` 文件夹中则不受影响。验证后仍异常通常是 Mod 残留的配置文件或加载器文件与恢复后的原始文件不匹配。

# 解决方案

1.验证完整性后，重新安装仍在使用的脚本类文件（ScriptHookV 等）
2.替换类 Mod 建议改用 OpenIV 的 `mods` 文件夹安装，避免被验证覆盖
3.验证后若仍崩溃，检查是否有残留的 `.asi`、`dinput8.dll` 等文件与新恢复的文件版本不匹配
4.养成备份习惯：对已替换的原始文件保留备份副本

# 相关文件路径

- `Steam\steamapps\common\Grand Theft Auto V\`（游戏根目录）
- `Steam\steamapps\common\Grand Theft Auto V\mods\`

# 常见错误日志

- Steam 提示 `Verifying integrity of game files...` 后大量文件被替换
- 验证后 `ERR_GEN_VERIFY (1)` 报错
