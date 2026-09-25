# 爱弥斯智能体

一款通用游戏故障诊断与修复工具：自动识别游戏技术栈（引擎 + 运行时 + 平台），解析日志与环境信息，结合本地知识库与多源联网检索定位根因，并以「通用修复原语 + 分级授权 + 可回滚」的方式自动执行修复。

内置两套知识库能力：

- **SQLite 知识库**（`gamedoctor.knowledge`）：错误签名 → 修复模板的精确/模糊匹配，沉淀成功修复经验。
- **RAG 向量知识库**（`rag/`）：基于 LangChain + Chroma，把各游戏的排障 Markdown 文档向量化后做语义检索，供大模型生成有据可依的答案。

> 详细产品设计见 [prd.md](./prd.md)。
>
> **当前状态（2026-09-11）**：多智能体协作已升级为「**三省六部式制度化架构**」
> （状态机 + 分权制衡）：接待官分拣 → 规划官起草（LLM 动态规划/模板兜底）→
> 审议官强制审议（可封驳 ≤3 轮）→ 调度官权限派发 → 领域智能体执行 →
> 验收 → 回奏；任务票全程留痕（flow_log），智能体不可越级、不可横向调用。
> 指纹识别层（运行时 / 平台 / 环境 / 文件）已全部替换为真实探测。
> WPF 桌面端为新拟物（Neumorphism）风格聊天界面，支持编辑/方案双模式、思考过程展开、
> 思考强度三档与联网搜索开关。**124 项测试通过**。

## 当前进度

### 已完成（可实际使用）

| 模块 | 说明 |
|---|---|
| `models.py` | 跨层数据契约（枚举 + dataclass），序列化/反序列化齐全 |
| `cli.py` | 命令可用：`diagnose`（`--fix` / `--dry-run` / `--step`）、`rollback`、`feedback`、`config`（含 `set-secret` / `path`）、`chat`、`agents`、`analyze`、`rag-index` |
| `config.py` | TOML 配置 + 系统 Keyring 存密钥；GUI 设置页可运行时注入（settings.json），优先级高于 config.toml |
| `pipeline.py` | 六层串联编排，任一层失败降级而不阻断输出；错误正则分类已覆盖 8 个通用类别 |
| `llm/` | **已接通**：`HttpLLMClient` 适配任意 OpenAI 兼容端点（deepseek / Qwen / 智谱 GLM / Ollama 等）；密钥解析顺序为「显式注入 → Keyring → 环境变量」 |
| `llm/agent_router.py` | **对话编排（方案三）**：入口 `detect_intent` 正则分流 → 确定性工作流 → 出口 `generate_reply` 解释；未命中才降级一次 LLM 智能体决策；`thinking` 全链路回显 |
| `searcher/` | 多源联网检索可用：Tavily / SerpAPI（带 key 优先）→ DuckDuckGo HTML（免 key 兜底），逐源降级、每次诊断限流最多 5 次查询 |
| `fixer/` | 原语注册表 + 分级授权（L0–L3）+ 事务执行器（备份→执行→验证→回滚）+ 备份管理 |
| 5 个可用原语 | `quarantine_file`、`clean_cache`、`append_launch_arg`、`edit_config`、`replace_text` |
| `textfile.py` + `fixer/text_edit.py` | 文本编辑能力（编码/行尾保真 + 替换 / ini / json / kv 改值） |
| `fingerprint/runtime.py` | **真实探测**：DirectX（特征 DLL）、VC++ 运行库（卸载注册表）、.NET（Release 映射）、GPU 与驱动（PowerShell CIM，自动过滤虚拟显示器）、Java、Wine/Proton |
| `fingerprint/platform.py` | **真实识别**：Steam（注册表 → libraryfolders.vdf → appmanifest，含 AppID）、Epic（Manifests/*.item）、GOG（goggame-*.info），standalone 兜底 |
| `fingerprint/engine.py` | 文件命名模式匹配可用 |
| `analyzer/env.py` | **真实采集**：磁盘余量（<20GB 告警）、内存压力（≥85% 告警）、高占用进程 Top8（psutil）、文件系统大小写敏感度实测；psutil 缺失时标准库降级 |
| `analyzer/files.py` | **真实扫描**：BepInEx/MelonLoader 共存冲突、零字节文件、大小写同名冲突、Unity/UE 关键文件缺失（只读，2 万文件上限） |
| `analyzer/logs.py` | 日志路径映射表 + 级别粗分 |
| `knowledge/` | SQLite 知识库可用（建表、upsert、签名模糊查） |
| `rag/` | RAG 向量知识库可独立运行：Minecraft 21 篇、GTA5 12 篇 |
| `orchestrator/` | 4 个工作流模板（安装/排障/调优/维护）+ 阶段依赖 + 阶段内并行 + **任务级门控降级** + optional 任务 + priority 排序 + tracker 阶段进度回显（制度中担任「调度官」执行器） |
| `governance/` | **三省六部式制度层**：任务票状态机（Intake/Planning/Review/Assigned/Doing/Verification/Done + 封驳回路）、角色权限矩阵、规划官（LLM 动态起草+模板兜底）、审议官（硬伤封驳/软提示/验收复审）、协调器（全链路驱动 + flow_log） |
| `agents/` | 13 个领域智能体；安全/启动优化等已接真实系统数据（见下文智能体表） |
| `server.py` | FastAPI 后端：`/chat`、`/run`、`/analyze`、`/agents`、`/skills`、`/mcps`、`/settings/*` |
| `desktop/` | WPF 桌面端：新拟物风格聊天界面 + 编辑/方案双模式 + 思考过程展开 + 思考强度三档 + 联网搜索开关 + 技能/MCP 管理页 |
| `report.py` | Rich 报告渲染 |
| `tests/` | **124 项测试通过**（制度层 39 项、编排器 23 项、指纹/文件扫描 8 项、LLM 客户端、搜索降级链、文本编辑回滚等） |

### 部分完成（可用但不完整）

| 模块 | 现状 | 缺什么 |
|---|---|---|
| `solver/generator.py` | LLM 方案生成链路已由 `agent_router` 对话编排覆盖 | CLI `diagnose` 路径的自动修复动作生成仍依赖知识库匹配 |
| `solver/critic.py` | 结构校验已生效（未知原语剔除 / 必填参数 / L 级别核对） | 语义层审查（动作与错误证据的相关性） |
| 平台修复原语 | `steam_verify_integrity` / `epic_repair` / `install_runtime` 有接口与策略 | 实际调用 steamcmd / Epic CLI / 运行时静默安装 |
| `installation` 智能体 | 安装规划、路径优选、磁盘检查、完整性验证可用 | 真正下载/安装游戏（需对接平台 CLI，属独立大功能） |
| `social` / `community` / `audio` 智能体 | 本地信息采集可用 | 平台好友 API、社区教程聚合、WASAPI 设备枚举（依赖第三方授权/API） |
| `analyzer/logs.py` | 路径映射 + 级别粗分可用 | 按引擎精解析（时间戳/堆栈聚合） |

### 待办优先级（建议顺序）

| 优先级 | 事项 |
|---|---|
| **P0** | `installation` 对接 steamcmd / Epic CLI 实现真实下载安装；平台修复原语落地 |
| **P1** | `feedback` → SQLite 修复模板沉淀闭环（目前仅打印）；`solver/critic` 语义审查 |
| **P2** | 知识库扩充：Minecraft 排障域尚有 30 项高频缺口（依据见 `docs/minecraft_kb_coverage.md`）；社区/社交智能体接真实 API |
| **非功能** | `rag/rag_data.py` 硬编码 API Key 需移除；`rag/minecraft/minecraft.md` 是空文件待删；`rag/minecraft/rag_data.md` 应改名 `game_version_mismatch.md`；launcher.py 后端 stdout 改落日志文件；`dotnet publish` 单文件发布打包 |

### 近期完成（2026-09-11）

- **多智能体架构升级为三省六部制**：新增 `governance/` 制度层——任务票状态机（含封驳/封还回路，越级跳转直接抛 `TransitionError`）、角色权限矩阵（执行层禁止横向调用）、规划官（LLM 在智能体目录内动态起草任务 DAG，模板 0 LLM 兜底）、审议官（未知智能体/依赖环/方案模式写操作等硬伤强制封驳，≤3 轮；安全前置缺失只给软提示）、协调器全链路驱动；`AgentChatService` 瘦身为薄壳。新增 39 项单测，全量 124 项通过；真实智能体冒烟：诊断模板 6 任务经一轮审议准奏并执行成功。

### 历史完成（2026-09-10）

- **方案三落地**：`detect_intent` 正则入口分流接入 `chat()`——命中工作流模板走确定性链路（0 次决策 LLM 调用），未命中才降级问一次 LLM；方案模式硬拦截含写操作的工作流（`MUTATING_WORKFLOWS`），只读排障放行；出口统一 `generate_reply`。
- **编排器加固**：串行阶段按 `(priority, name)` 排序；门控粒度从「按阶段」放宽到「按任务」（一个只读采集失败不再连带阻断下游）；optional 任务失败不计入整体成败；每阶段结束输出聚合进度摘要。
- **指纹层真实化**：env / platform / runtime / files 四个骨架模块全部替换为真实探测（详见上表），安全智能体反作弊识别升级为文件特征 + 运行进程双路（EAC / BattlEye / Vanguard / Ricochet），启动优化智能体后台清理改为 psutil 真实枚举。
- **桌面端 UI**：新拟物（Neumorphism）设计系统（`NeuPanel` 凸起/凹陷控件、Hover 阴影收缩、按下凹陷转态）；4pt 间距尺度重排；设置面板改凹槽消除卡片嵌套。
- **测试**：修复 searcher/critic/llm_client 三处既有漂移（共 7 项失败），新增编排器 23 项与指纹扫描 8 项，全量 85 项通过。

## 对话编排：三省六部式制度化协作（状态机 + 分权制衡）

多智能体协作**不是自由对话，而是走流程**（参照「三省六部」运行架构）：
每轮对话是一张任务票（TaskTicket），只能沿状态机单向递进；规划、审议、
执行三权分立，智能体自身无权改任务状态。

```text
接待官 Intake ─── 意图分拣（正则）/ 闲聊直答 / 建票（GD-YYYYMMDD-NNN）
   │  └─ 散点问答无需立项 → 直接单智能体决策直办（Intake→Done）
   ▼
规划官 Planning ── 需求 → 任务 DAG
   │               · 命中 4 类模板：取代码锁死安全顺序的工作流（0 次 LLM）
   │               · 自由/复合需求：LLM 在已注册智能体目录内动态起草（1 次 LLM），
   │                 只能「选将」不能「造将」；LLM 失败则封还接待官直办
   ▼
审议官 Review ─── 纯规则强制审议（不调 LLM），不可跳过
   │               硬伤封驳：未知智能体 / requires 缺失 / 依赖成环 /
   │               方案模式出现写操作；软提示：装 Mod 前缺反作弊检测等
   │  ┌────────────┘ 封驳附问题清单退回规划官，最多 3 轮；
   │  │              硬伤 3 轮不除 → 任务终止（安全红线绝不强制通过）
   ▼ ▼
调度官 Assigned ── 逐任务过权限矩阵白名单后派发（非法任务剔除）
   ▼
执行   Doing ──── 15 个领域智能体：阶段并行 / 任务级门控 / 异常隔离
   ▼
验收   Verification ── 必选任务成败核定（执行失败不重规划，如实回奏）
   ▼
完成   Done ───── 回奏：generate_reply 把结构化结果转自然语言（失败降级统计摘要）
```

**角色权限矩阵（不可越级）**：接待官→规划官；规划官→审议官；审议官→规划官（封驳）/调度官（准奏）；
调度官→已注册领域智能体；**执行智能体之间没有横向调用权**，只能回报调度官。

- **方案模式（plan）**：审议官硬拦截一切写操作计划（`installation/game_content/script_editor` 与 download），只读排障放行；编辑模式（edit）为自动化执行者。
- **LLM 预算**：模板路径仅出口 1 次调用；动态规划路径规划 1 次（封驳每轮 +1，至多 3 次）+ 出口 1 次；审议 0 次。
- **可观测**：每次状态转移写 `flow_log`（谁→谁/备注/时间），过程写 `thinking`（`[接旨]/[规划]/[审议·封驳]/[调度]/[执行]/[验收]/[回奏]`），返回体含 `ticket`（票号/状态/审议轮次/完整流转链），桌面端思考面板可直接展开。
- **思考强度三档**：轻度/中度/重度映射 temperature 0.1/0.4/0.7 与 max_tokens 512/1024/2048。
- **联网搜索**：默认关闭；开启后搜索智能体才进入审议白名单，LLM 可检索解决方案或下载文件。
- 代码位置：`governance/`（state_machine / permissions / planner / reviewer / coordinator）；原 `orchestrator/` 保留为调度官的执行器。

## 智能体系统（多智能体编排）

基于 `BaseAgent` 抽象基类实现的领域智能体集群，由编排器统一调度，用于扩展「游戏辅助安装生态」：

| 智能体 | 能力 |
|---|---|
| `compatibility` | 游戏与系统 / 硬件兼容性检测 |
| `installation` | 安装源选择、路径优化、安装计划、磁盘空间检查 |
| `game_content` | 游戏内容管理（按 op 分流）：`version` 版本对比/更新风险/回滚、`saves` 存档备份/恢复/云同步/迁移（写操作前自动备份）、`dlc` DLC·Mod 管理/冲突检测/版本同步 |
| `launch_optimizer` | 启动参数优化、后台清理（psutil 真实枚举高占用进程）、预加载建议 |
| `community_agent` | 教程 / 视频 / 社区讨论聚合 |
| `social_agent` | 好友状态、语音频道、隐私设置 |
| `perf_security` | 运行体检（按 op 分流）：`performance` CPU/内存/显存瓶颈（工作流中可基线→调优→复测两次调用）、`security` 反作弊双路识别（文件特征 + 运行进程：EAC/BattlEye/Vanguard/Ricochet）/恶意进程/隐私；缺省两项全检 |
| `audio_expert` | 音频设备、格式兼容、环绕声 |
| `network_expert` | 延迟、NAT、端口、服务器推荐 |
| `log_analyzer` | 游戏日志定位、错误级别粗分与关键行提取 |
| `script_editor` | 配置/脚本安全编辑（自动备份，锚点替换） |
| `web_search` | 联网检索与下载（**仅在「联网搜索」开关开启时注册**，走 Tavily/SerpAPI/DDG 降级链） |

- **注册**：`orchestrator/agent_factory.py` 的 `DEFAULT_AGENT_CONFIGS` 数据驱动注册；新增智能体需同时加入工厂配置与编排路由。
- **编排**：4 个预定义工作流模板（`create_installation_workflow` / `create_diagnostic_workflow` / `create_optimization_workflow` / `create_maintenance_workflow`），阶段间串行、阶段内可并行。
- **路由**：任务**按智能体注册名直接路由**（`AgentTask.agent`），刻意不走能力匹配——多个智能体能力有重叠（如 `file_read` 同属日志分析与脚本编辑），按名路由最可靠。
- **门控两级**：阶段级 `dependencies`（前置阶段必选任务失败则整段跳过）+ 任务级 `requires`（前置任务失败/跳过只跳过该任务，兄弟任务照常）；`optional=True` 的增益任务失败不影响整体成败。
- **安全前置顺序由代码锁死**：`perf_security`(security) 在 `game_content`(dlc) 之前（未过反作弊检测装 Mod 有封号风险）、`game_content`(saves) 在任何写操作之前、`game_content`(version) 在 Mod 版本同步之前、`social_agent` 在 `network_expert` 之后。同一智能体的不同子任务以 name 区分、由 requires 锁序。
- **执行模型**：`execute` / `execute_workflow` 均为 `async`，CLI 与测试通过 `asyncio.run` 触发。

## 快速开始
克隆代码仓库
git clone https://github.com/XiyueElycis/Aemeath.git

### 安装核心依赖

```bash
# 开发模式安装（含 pytest / ruff）
pip install -e ".[dev]"
```

### CLI 使用

```bash
# 查看命令
gamedoctor --help

# 诊断（只读，不修改）
gamedoctor diagnose "某游戏" --error "0xc000007b"

# 自动修复（L1 白名单直修 + L2 逐项确认 + 全程备份可回滚）
gamedoctor diagnose "某游戏" --error "..." --fix

# 干跑：只看将执行的动作，不实际改动
gamedoctor diagnose "某游戏" --error "..." --fix --dry-run

# 单步模式：每个动作执行前逐项确认
gamedoctor diagnose "某游戏" --error "..." --fix --step

# 一键回滚
gamedoctor rollback --id <诊断ID>

# 反馈（沉淀修复模板）
gamedoctor feedback --id <诊断ID> --result fixed

# 配置与密钥管理（密钥走系统 Keyring，不明文落盘）
gamedoctor config set-secret <key> <value>
gamedoctor config path

# 多智能体：交互式会话（类 Claude Code 的 REPL 模式，对话编排走方案三）
gamedoctor chat --game "某游戏"

# 多智能体：启动 HTTP 后端（供下面「桌面客户端」调用）
gamedoctor-server

# 多智能体：列出所有智能体
gamedoctor agents

# 多智能体：快速分析（仅兼容性检测）
gamedoctor analyze "某游戏"

# 多智能体：完整分析（多阶段编排：兼容性 + 性能 + 网络等）
gamedoctor analyze "某游戏" --full
```

密钥 key 格式：`llm/<provider>/api_key`、`search/<provider>/api_key`。

## 桌面客户端（WPF，C# / .NET）

原生 Windows 图形界面，采用 **C# WPF 前端 + Python FastAPI 后端** 架构，XAML 布局。

### 一键启动（推荐）

```bash
pip install -e ".[server]"
gameAgent            # 同时拉起后端 + WPF 前端；关闭窗口后自动结束后端
```

> `gameAgent` 若不在 PATH，改用 `python -m gamedoctor.launcher`，或把
> `%APPDATA%\Python\Python3xx\Scripts` 加入 PATH。

### 手动分步启动

1. 启动后端服务：

   ```bash
   pip install -e ".[server]"
   gamedoctor-server            # 默认监听 127.0.0.1:8765
   ```

2. 构建并运行 WPF 前端：

   ```powershell
   cd desktop\GameDoctor.Desktop
   dotnet build -c Debug
   dotnet run                   # 或直接运行 bin\Debug\net9.0-windows\GameDoctor.Desktop.exe
   ```

界面为主窗口聊天式工作台（新拟物 Neumorphism 设计系统，`NeuPanel.cs` 凸起/凹陷控件）：

- **对话区**：左主对话气泡（助手凸起灰 / 用户强调色右对齐）+ 右侧可堆叠调节区；「正在输入」三点动效；气泡可展开查看 AI 思考过程（`[分流] → [执行] → [回复]`）。
- **模式切换**：编辑模式（自动化执行者）/ 方案模式（只读分析顾问，写操作工作流入口拦截）。
- **调节项**：思考强度三档（轻度/中度/重度，默认收纳）、联网搜索开关。
- **设置弹窗**：模型 provider / model 选择、API Key 输入（存入 Keyring）、智能体启用清单。
- **技能管理页 / MCP 管理页**：独立 Tab，凹陷列表 + 圆角悬停项。
- 顶部「全部分析」与右侧「智能体状态」卡仍保留单智能体手动执行入口。

后端地址可用环境变量 `GAMEDOCTOR_API` 覆盖（默认 `http://127.0.0.1:8765`）。

后端 REST 接口（见 `src/gamedoctor/server.py`）：

| 方法 / 路径 | 用途 |
|---|---|
| `POST /chat` | 对话主入口。请求 `{message, game_dir, history, enable_search, think_level(light/medium/heavy), mode(edit/plan)}`；返回 `{reply, agent_used, result, thinking}` |
| `POST /run` | 手动执行单个智能体（绕过 LLM 决策） |
| `POST /analyze` | 执行多个（默认全部）智能体，返回结果列表 |
| `GET /agents` / `GET /skills` / `GET /mcps` | 智能体 / 技能 / MCP 清单 |
| `GET /status` / `GET /settings` | 运行状态与当前设置 |
| `GET /settings/api-key/{provider}` | Key 是否已配置（脱敏） |
| `POST /settings/api-key` / `DELETE /settings/api-key/{provider}` | 写入 / 删除 API Key |
| `POST /settings/llm` / `POST /settings/agents` / `POST /settings/skills` / `POST /settings/mcps` | 更新模型、智能体启停、技能、MCP 配置 |

> **API Key 配置优先级**：运行时显式注入（设置页/settings.json）> Keyring > 环境变量。
> Key 不生效时优先检查是否被环境变量覆盖；密钥格式为 `llm/<provider>/api_key`、
> `search/<provider>/api_key`。

### RAG 知识库使用

```bash
pip install langchain-deepseek langchain-community langchain-text-splitters langchain-core chromadb sentence-transformers
```

```python
from rag.rag_data import RAGKnowledgeBase

kb = RAGKnowledgeBase(data_root="rag")   # 默认就是 rag/ 目录
kb.index_all()                            # 为所有游戏建索引

chunks = kb.search("minecraft", "找不到 Java 运行环境")
answer = kb.ask("minecraft", "启动器提示找不到 Java 怎么解决？")
```

每款游戏对应一个子目录，文档格式为带 YAML front-matter 的 Markdown（见下文「知识库文档格式」）。

## 项目结构

```text
├── prd.md                    # 产品设计
├── docs/                     # 评估与调研文档
│   └── minecraft_kb_coverage.md  # Minecraft 知识库覆盖率评估
├── pyproject.toml            # 依赖与打包配置
├── src/gamedoctor/           # 诊断核心（分层架构）
│   ├── fingerprint/          # ① 指纹识别层（引擎 / 运行时 / 平台）
│   ├── analyzer/             # ② 信息采集层（日志 / 文件 / 环境）
│   ├── knowledge/            # ③ 本地知识库（SQLite RAG + 可选向量检索）
│   ├── searcher/             # ③ 多源联网检索（搜索模板 + 数据源适配器）
│   ├── llm/                  # ④ LLM 接入（OpenAI 兼容客户端）+ agent_router 对话编排
│   ├── solver/               # ④ 方案生成（机器可执行动作序列）与交叉自检
│   ├── fixer/                # ⑤ 自动修复（原语库 / 分级授权 / 事务化执行 / 备份回滚）
│   │   ├── primitives/       #   修复原语库（文件 / 文本 / 配置 / 平台 / 运行时）
│   │   └── text_edit.py      #   文本编辑算法（替换 / ini / json / kv 改值）
│   ├── textfile.py           #   文本文件安全读写底座（编码 / 行尾保真）
│   ├── models.py             # 跨层数据契约（枚举 + dataclass）
│   ├── pipeline.py           # 六层串联编排
│   ├── agents/               # 领域智能体集群（15 个：12 领域 + 日志/脚本/联网，+ BaseAgent 基类）
│   ├── governance/         # 三省六部制度层：状态机/权限矩阵/规划官/审议官/协调器
│   ├── orchestrator/       # 智能体工厂 + 调度官执行器（4 工作流模板 / 阶段并行 / 两级门控）
│   ├── utils/                # 系统信息收集 / 依赖检查
│   ├── cli.py                # Typer 命令行入口
│   ├── repl.py               # 交互式会话（类 Claude Code 的 REPL）
│   ├── launcher.py           # 一键拉起后端 + WPF 前端（gameAgent）
│   └── server.py             # FastAPI HTTP 后端（对话 / 智能体 / 设置，见上文接口表）
├── desktop/                  # C# WPF 桌面客户端（.NET 9 + XAML）
│   └── GameDoctor.Desktop/
│       ├── App.xaml          #   新拟物设计系统（设计令牌 / 控件样式）
│       ├── NeuPanel.cs       #   新拟物卡片控件（凸起 / Inset 凹陷 + 双投影）
│       ├── MainWindow.xaml   #   聊天工作台（对话区 / 双模式 / 思考展开 / 设置弹窗）
│       ├── SkillsPage.xaml   #   技能管理页
│       ├── McpPage.xaml      #   MCP 管理页
│       └── AgentApiClient.cs #   后端 HttpClient 封装
├── rag/                      # RAG 向量知识库（LangChain + Chroma）
│   ├── rag_data.py           # 多游戏适配器 + 检索/生成接口
│   ├── Grand_Theft_Auto_V/   # GTA5 排障知识文档（12 篇）
│   └── minecraft/            # Minecraft 排障知识文档（21 篇，另有 1 个空文件待清理）
└── tests/                    # 单元测试（124 项：制度层 39 / 编排器 23 / 指纹扫描 8 / LLM / 搜索 / 编辑回滚等）
```

## 关键设计约定

- **修复原语**：修复动作按操作类型组织（文件 / 文本 / 配置 / 平台 / 运行时），由技术栈指纹填参，而非为每个游戏写死脚本。见 `fixer/primitives/base.py`。
- **分级授权**：L0 只读 / L1 安全自动 / L2 需确认 / L3 禁止自动。见 `fixer/policy/levels.py`。
- **事务化闭环**：备份 → 执行 → 验证 → 回滚。见 `fixer/executor/transactional.py`。
- **核心数据契约**：所有跨层传递的数据结构统一定义在 `models.py`。
- **多游戏知识库适配**：`rag/rag_data.py` 通过 `GameKnowledgeAdapter` 接口预留扩展点，每个游戏一个适配器实例、独立的文档目录与向量集合；接入新游戏只需新增同名子目录放入 `*.md`，或注册自定义适配器。
- **文本编辑闭环**：先读后改 —— `analyzer.peek_text_file()` 取原文给 LLM 生成替换锚点，`replace_text` / `edit_config` 两个原语负责改，改完由事务执行器完成备份 / 验证 / 回滚。见 `textfile.py`（IO 保真）与 `fixer/text_edit.py`（改法算法）。

## 文本编辑能力

给 LLM 的"机器可执行动作"只有两种就够覆盖绝大多数场景，新增改法应当扩展它们的参数而不是新增大量原语。

| 原语 | 用途 | 关键参数 | 默认级别 |
|---|---|---|---|
| `replace_text` | 自由文本 / 按锚点替换（支持正则） | `path`、`old`、`new`、`regex`、`count`、`expect`、`absent` | L2 |
| `edit_config` | 结构化改键值（ini / json / kv 自动识别） | `path`、`key`、`value`、`section`、`format`、`sep`、`add_if_missing` | L2 |

```json
{"id": "a1", "primitive": "replace_text",
 "params": {"path": "…/options.txt", "old": "maxFps:260", "new": "maxFps:60", "expect": 1},
 "level": "L2", "description": "限制帧率上限", "verify_checkpoint": "配置中出现 maxFps:60"}
```

安全约定（由框架兜住，原语作者无需重复实现）：

- `TextDoc` 保真三件套 —— **编码 / 行尾 / 末尾换行**，UTF-16、GBK、CRLF 的配置不会被写坏；
- ini / kv 走**按行精改**，注释与排版保留（configparser 会破坏它们，故不使用）；
- `expect` 不符或锚点未命中时**一个字节都不改**；验证检查点不通过则**自动回滚备份**；
- `--dry-run` 会调用原语的可选 `preview()` 输出 diff，做到"先审后改"。

## 知识库文档格式

`rag/<游戏名>/*.md`，文件头为 YAML front-matter，正文按固定小节组织：

```markdown
---
title: "启动器找不到 Java 运行环境"
game: "Minecraft"
mod_name: NULL
category: "Java 运行环境问题"
tags: ["java", "launcher", "runtime", "无法启动"]
---

# 问题描述
...

# 症状表现
...

# 根因分析
...

# 解决方案
...

# 相关文件路径
- ...

# 常见错误日志
- ...
```
