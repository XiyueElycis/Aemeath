# 爱弥斯控制台 · 星海深靛 × 爱弥斯粉 配色重构 + 游戏化动效

## Context

用户要求舍弃现有「白底蓝粉」配色，重新设计一版**深色游戏 HUD 风格**的主界面，并与启动动画无缝衔接；同时把启动动画配色改为符合爱弥斯角色（星海轻歌的电子幽灵）的美术风格。

用户已拍板两个方向：
1. **深色星海 HUD**：全系统（启动动画 / 主控制台 / 心动模式）统一「星海深靛 × 爱弥斯粉」，启动动画结尾从「过曝洗白进浅色」改为「洗入星海深靛」。
2. **爱弥斯粉 #F0558E 系升为主操作色**（按钮/选中态/发送），冰蓝退为系统数据色，青绿保持状态数据色。

设计锚点：`src/aemeath_agent/galgame/web/styles.css` 现有「星海夜航」变量（`--night-1:#0e0a1f`、`--rose-2:#f472b6` 等）即为新方向基准，心动模式无需改动即天然同源。

保留约束：不做双主题；不改信息架构与功能；WPF 动画只用 Opacity/Transform；「减少动画」开关（`Fx.MotionEnabled`）继续门控所有动效。

## 一、色板（App.xaml 令牌映射）

### 结构色
| 令牌 | 新值 | 说明 |
|---|---|---|
| BgBrush | `#0E0A1F` | = night-1，三界面统一底色 |
| SurfaceBrush | `#1A1233` | = night-2，面板/侧栏 |
| WellBrush | `#120D26` | 凹槽比底色更深 |
| WellHoverBrush | `#221842` | ≈ night-3 提亮 |
| BorderBrush | `#2F2154` | |
| BorderStrongBrush | `#453170` | |

### 语义色
| 令牌 | 新值 |
|---|---|
| AccentBrush | `#4A8AF0`（系统数据色，深底提亮） |
| AccentDeepBrush | `#6FA3F5`（深色下「深」改「亮」变体） |
| AccentSoftBrush | `#1D2748` |
| DataAccentBrush | `#55CBB0`（不变） |
| DataAccentSoftBrush | `#12332C` |
| StatusOkBrush / StatusBusyBrush / StatusErrorBrush | `#55CBB0` / `#FFC98A` / `#F07A7A` |
| **PinkBrush（主操作色）** | `#F472B6`（= rose-2） |
| PinkDeepBrush | `#D24E97` |
| PinkSoftBrush | `#331E3D`（选中态玫瑰暗底） |
| BluePanelBrush / BlueAccentDeepBrush | `#1B2340` / `#6FA3F5` |

### 文字与气泡
| 令牌 | 新值 |
|---|---|
| TextPrimaryBrush | `#EDE9F7` |
| TextSecondaryBrush | `#9C93B8` |
| BubbleAssistantBrush / LineBrush | `#241635` / `#40FFAED8`（25% 粉描边） |
| BubbleUserBrush / LineBrush | `#1C2540` / `#404A8AF0` |

深色下气泡策略：双气泡都是深玻璃暗底 + 各自语义色细描边（与 galgame msg-aemeath 同构），正文统一浅紫白。

### 玻璃拟态近似（WPF 无 backdrop-filter）
- NeuPanel 阴影：`#050308` Blur 16 / Depth 2 / Opacity 0.50
- ComboBox Popup 阴影：`#050308` Blur 18 / Depth 4 / Opacity 0.60
- CTA 悬停辉光：`#F472B6` Blur 14 / Depth 0 / Opacity 0.45

### 模板内散落硬编码（随同改）
- NeuFocusVisual 焦点环 `#2E6BE6` → `#F472B6`
- Button HoverOverlay `#0A2E6BE6` → `#14F472B6`；PressOverlay → `#2EFFFFFF`
- TextBox/PasswordBox/SelectableText：CaretBrush → `#EDE9F7`、SelectionBrush → `#3D4A8AF0`、聚焦青绿光晕 Opacity 提到 0.45
- ComboBox 箭头 → `#9C93B8`
- Slider Thumb：底 `#2A1E4D`、边 `#F472B6`
- **选中态全面换粉**：ToggleButton / ListBoxItem / ComboBoxItem 选中 → PinkSoftBrush 底 + PinkBrush 边 + `#FF8FC7` 字；CheckBox / RadioButton 选中主体 → 粉；SideNavButtonStyle 左指示条 + 悬停 → 粉系
- NeuAccentButtonStyle 与 PinkAccentButtonStyle 统一为粉 CTA（bg `#F472B6` / 边 `#D24E97` / 白字）
- MainWindow GridLayer 画笔 `#082E6BE6` → `#164A8AF0`（深底提到 ~9% 可见）
- HeartDimOverlay → `#060410`（主底已深，压暗层须更深才有「入夜」感）

## 二、启动动画重设计（startup/web 三件套）

- 底色：`--d-0` `#050708`→`#0E0A1F`、`--d-1`→`#141026`、`--d-2`→`#1A1233`；StartupWindow.xaml(.cs) 背景成对改 `#0E0A1F`。
- **粉色放宽到 ~6 处**（主视觉预告）：brandName、ringSight 中心脉冲、ph4 认证按钮、ph5 进度条渐变（`#D24E97→#F472B6→#FFC98A`，同 galgame 好感度条）、seq 日志关键字、ph7 欢迎语+心电波。HUD 六项数值保持青绿。
- **阶段 7「洗入星海」**：`#whiteout` → `#washin`，背景 `radial-gradient(ellipse at 50% 45%, #1A1233, #0E0A1F)`，动画 0.8s opacity 0→1；**删除 `body.light` 与全部 `--l-*` 浅色变量/规则**（boot.js 中 `classList.add("light")` 移除，ph7 文字直接 `#EDE9F7` / `#FF8FC7`）。洗入终点 = 主界面 BgBrush = StartupWindow 底色，三重对齐。
- **启动动画星空层**：index.html 加 `<div class="starfield" id="starfield"><i></i><i></i><i></i></div>` 三层；boot.js 新增 `generateStars()`（box-shadow 星点 60/35/15 颗，色 `rgba(237,233,247,.9)` / `rgba(255,143,199,.75)` / `rgba(74,138,240,.7)`）；style.css 三层 twinkle 动画（3.5/5/7s 错相）+ 容器 160s 微漂移 + `prefers-reduced-motion` 下隐藏。

## 三、主界面游戏化动效

### 1. 星空粒子背景层（新建 `StarfieldLayer.cs`，唯一新文件）
- `StarfieldLayer : Canvas`，Loaded 时按 `面积/16000` 生成星点（上限 120），Ellipse 直接设 Canvas.Left/Top，`IsHitTestVisible=False`，Brush 全部 Freeze。
- 分布：70% `#EDE9F7` / 18% `#FF8FC7` / 12% `#4A8AF0`；1–2.4px，随机 6 颗 3px 主星带光晕；基础 Opacity 0.25–0.9。
- 闪烁用 **Storyboard**（每星一条 DoubleAnimation：Opacity AutoReverse Forever，Duration 2–6s 随机，BeginTime 错相；聚合一个 Storyboard 一次 Start）。纯 Opacity 动画走合成线程，无布局失效。
- 挂载：MainWindow.xaml GridLayer 之后、HeartDimOverlay 之前，`Grid.ColumnSpan="2"`。
- 门控：公开 `EnableMotion(bool)`；`ApplyUiPrefs` 中 `Stars.EnableMotion(!_uiPrefs.ReducedMotion)`；关闭时星点静止、容器 Opacity 0.55。

### 2. CTA 玫瑰辉光 + 按钮扫光（App.xaml 模板）
- CTA IsMouseOver 静态 Setter 触发 `#F472B6` 辉光。
- Button 模板加 70px 宽 `#26FFFFFF` 渐变扫光 Rectangle（TranslateTransform），IsMouseOver 触发一次性 0.5s 平移。

### 3. HUD 数据刷新闪动（MainWindow.xaml.cs）
- `TickHud` 更新数值后对 HudStrip 值文本播 150ms Opacity 1→0.55→1 闪动（经 `Fx.MotionEnabled` 门控）。

已有动效（入场/扫描线/状态灯/圆点/心动转场）仅换色不动逻辑。

## 四、文件级改动清单

| 文件 | 改动 |
|---|---|
| `desktop/…/App.xaml` | 全部令牌换值；模板硬编码色 ~10 处；选中态蓝→粉；Button 扫光；CTA 辉光 |
| `desktop/…/MainWindow.xaml` | GridLayer 画笔；插入 StarfieldLayer；HeartDimOverlay 色 |
| `desktop/…/MainWindow.xaml.cs` | ApplyUiPrefs 挂 Stars.EnableMotion；TickHud 加闪动 |
| `desktop/…/StarfieldLayer.cs` | **新建**（唯一新文件） |
| `desktop/…/SandboxWindows.cs` | KindBrush 4 色：write `#FF9860` / download `#A78BFA` / install `#FF8FC7` / 默认 `#4A8AF0` |
| `desktop/…/ThinkingLogConverter.cs` | 前缀 `#6F6690` / 键名 `#55CBB0`（不变）/ 正文 `#8D84A8` |
| `desktop/…/StartupWindow.xaml(.cs)` | 背景成对改 `#0E0A1F` |
| `desktop/…/GalgameWindow.xaml(.cs)` | **无需改**（已同源） |
| `startup/web/index.html` | starfield 三层；whiteout→washin 改名 |
| `startup/web/style.css` | 底色三变量；粉放宽 6 处；washin 动画；删 `--l-*`/body.light；starfield 样式 |
| `startup/web/boot.js` | phase7 改 washin（去 body.light）；新增 generateStars() |
| `galgame/web/*` | 无需改动 |

## 五、验证

1. `dotnet build` 零警告零错误（注意 3 值 Margin 之类运行时才炸的 XAML 陷阱——上轮教训）。
2. **运行时冒烟**：启动 `AemeathAgent.Desktop.exe`，完整看动画 → 洗入星海 → 主界面底色与动画终点肉眼无接缝；开「减少动画」确认星点静止、扫描线/圆点/入场停用。
3. 无头截图：后端起服务后 Edge headless 截 ph1/ph5/washin 帧，比对 `#0E0A1F` 终帧（上轮已验证过的流程，用 dump-dom + screenshot 双证据）。
4. 心动模式开/关转场回归；Sandbox 四类徽章目检；设置页/技能页/对话框走查（全令牌化应自动跟随）。
5. 后端 pytest（406 项）确认不受影响（纯前端改动）。

## 风险点

- 深底 caret/选区必须显式亮色（已列入令牌）。
- ComboBox Popup 深底融合：需 BorderStrong 级描边。
- 高 DPI：边框保持 1px + SnapsToDevicePixels；星点 ≥1px + UseLayoutRounding。
- WebView2 白闪：XAML hex 与 .cs FromArgb 成对改。
- 性能：星点 ≤120、纯 Opacity 动画、Brush 冻结、辉光不动画 Effect 属性。
