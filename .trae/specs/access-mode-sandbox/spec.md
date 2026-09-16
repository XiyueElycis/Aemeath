# 智能体访问模式（沙箱授权 / 直接访问）- 产品需求文档

## Overview

- **Summary**：为智能体执行层新增一个与「方案/编辑模式」正交的安全维度——**访问模式（access_mode）**：
  - `sandbox`（沙箱授权，HTTP 默认）：每个任务执行前须经用户在桌面端授权；任务运行在副本沙箱中（读透传真实目录、写重定向到沙箱覆盖层）；全部任务完成后，用户审核变更清单，确认后才落盘到真实目录（落盘前自动备份），或丢弃变更。
  - `direct`（直接访问，CLI 默认）：维持现有行为，智能体直接在真实文件系统上操作。
- **Purpose**：当前智能体一旦经规划/审议即可直接修改、删除、下载游戏目录中的真实文件，仅有各 agent 自发的路径穿越防护与单文件备份，用户无法在"任务执行前"拦截、也无法在"落盘前"审阅实际改动。引入"用户授权 + 沙箱隔离 + 审核落盘"三段式控制，把智能体的文件系统破坏力收敛到可审阅、可回滚的范围内。
- **Target Users**：使用桌面端（WPF）处理游戏辅助任务的单机用户；CLI/脚本用户保留直接访问模式。

## Goals

- 用户在每个智能体任务执行前可见：调用的智能体、操作类型、目标路径/URL、读/写性质，并可批准或拒绝。
- 沙箱模式下，智能体的**任何**写操作（新建/修改/删除/替换/下载/建目录/备份输出）在用户"应用变更"之前都不触碰真实游戏文件。
- 变更审核：以文件为粒度展示变更（新建/修改/删除/下载），文本文件可预览新旧内容；应用前自动备份真实文件；应用/丢弃均有明确结果反馈。
- 与现有 plan/edit 模式正交并存，UI 各一个开关，两套语义互不干扰。
- 直接访问模式与现状完全一致，既有 133 项测试行为不回归。

## Non-Goals

- 不做进程级/系统级隔离（不用低权限令牌子进程、不用容器/虚拟机）。
- 不做操作系统权限提升或"以 Windows 用户身份重新认证"（UAC）；"以用户授权行动"在本期指**应用内显式任务授权**，不涉及操作系统账户令牌。
- 不做逐字节 diff 编辑器/三方合并；文本预览仅展示新旧全文（可选行级 diff 为加分项）。
- 不做沙箱内运行游戏/安装器本体（安装类智能体当前只做目录规划，不实际执行安装器）。
- 不改造 `gamedoctor/fixer/` 旧修复链路（当前编排不经过它）与任务票持久化体系。
- 不做多用户/远程审批；授权仅本机单用户。

## Background & Context

- 现有安全控制两层：
  1. 角色派发矩阵 [permissions.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/governance/permissions.py)：管"谁能调用谁"；
  2. 运行模式 `mode=edit|plan`：[reviewer.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/governance/reviewer.py) 在方案模式下硬封驳 `MUTATING_AGENTS`。
  两层都不回答"任务在哪片文件系统上执行、凭谁的即时授权"。
- 实际文件系统写操作面（已全量盘点，数量很少）：
  - [script_editor_agent.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/scripts/script_editor_agent.py)：`write_text`（edit/create，L164/L195）、`unlink`（delete，L217）、`copy2`（replace，L259）、自发备份 `_backup`（L349-361）；路径经 `_resolve_file`（已有 `relative_to` 穿越防护）。
  - [web_search_agent.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/web/web_search_agent.py)：下载 `write_bytes`（L152）、自发备份（L214-218）；路径经 `_resolve_dest`。
  - [save_manager_agent.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/saves/save_manager_agent.py)：备份 `copy2`（L110-113），输出到备份目录。
  - [installation_agent.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/installation/installation_agent.py)：`mkdir`（L138）。
  - dlc_manager / update_manager：无任何落盘调用（当前为规划/检测型）。
- 任务执行统一收口：[orchestrator.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/orchestrator/orchestrator.py#L229-L245) `_execute_task_with_context`，并行（gather）与串行阶段都经过它；单智能体直办路径经 coordinator 补走完整链路后同样过调度官 → orchestrator 执行。审批门单点挂载即可全覆盖。
- 票据身份：`GovTicket` 已含 `ticket_id`；执行时 `task.data` 已携带 `game_dir/game_context/timestamp` 等，并已有直接放置 Python 对象（`GameContext`）的先例，沙箱会话对象按同口径注入。
- 前端：模式开关在 [MainWindow.xaml](file:///d:/Python/游戏辅助安装智能体/desktop/GameDoctor.Desktop/MainWindow.xaml#L52-L63)；请求由 [AgentApiClient.ChatAsync](file:///d:/Python/游戏辅助安装智能体/desktop/GameDoctor.Desktop/AgentApiClient.cs#L219-L225) 以匿名 JSON 发送；错误/状态展示在 MainWindow.xaml.cs 事件流面板。
- 后端 FastAPI 端口 8765，已有全局异常处理器返回 JSON；桌面端与后端为同机进程。

## Functional Requirements

### 访问模式与参数

- **FR-1**：`POST /chat` 请求体新增 `access_mode`，取值 `sandbox|direct`；HTTP 通道缺省为 `sandbox`。CLI 新增 `--access/--no-sandbox` 等价选项，缺省 `direct`（脚本场景无人审批）。模式随票据在整条链路口径一致并体现在票据详情/flow_log 中。
- **FR-2**：WPF 模式行新增"访问：沙箱授权 / 直接访问"单选，与"编辑/方案"并排独立；选择状态在应用会话内保持；每次 /chat 请求携带 `access_mode`。
- **FR-3**：访问模式与 plan/edit 正交：plan+sandbox 下写操作仍被审议封驳（变更集天然为空、不弹落盘审核）；edit+sandbox 下写入进沙箱；任意模式 + direct 维持现状。

### 任务级授权门

- **FR-4**：sandbox 模式下，每个任务在 `requires` 门控通过之后、agent 实际执行之前挂起，生成一条授权请求：`{id, ticket_id, agent, operation, 目标(file/dir/url), 性质(只读/写入/下载/安装), 任务说明}`，并在事件流/思考过程可见"等待用户授权"。
- **FR-5**：后端提供 `GET /approvals/pending`（列出待决请求）与 `POST /approvals/{id}`（body: `{"decision":"approve|reject"}`）。批准→任务继续；拒绝→该任务返回"用户拒绝授权"的失败/跳过结果，下游依赖按既有 requires 规则跳过，链路不报错、不产生 500。
- **FR-6**：并行阶段多任务同时产生多条待决请求，UI 支持逐条批准/拒绝与"全部批准"。授权请求 10 分钟未决自动按拒绝处理（超时常量可配置），结果标注"授权超时"。
- **FR-7**：direct 模式不产生授权请求、不挂起；事件流记录一行"直接访问模式，跳过任务授权"。

### 副本沙箱

- **FR-8**：sandbox 模式按票据创建沙箱会话，根目录 `~/.gamedoctor/sandbox/<ticket_id>/`，含 `overlay/`（写入覆盖层，保持相对真实根的目录结构）、`side/`（游戏根外写入的重定向区）、`meta.json`（票据、真实根、时间、状态、变更清单）。**不**整盘复制游戏目录，仅被触碰文件按需进入 overlay。
- **FR-9**：读语义：文件在 overlay 存在则读 overlay，否则透传真实文件；已删除文件（tombstone）视为不存在。写语义：create/edit/replace/download/建目录全部重定向到 overlay；delete 在变更清单记 tombstone 且不删真实文件。游戏根之外的写（含各 agent 自发备份、存档备份输出）重定向到 `side/`，不允许写入真实位置。
- **FR-10**：所有经沙箱的路径解析必须保留并强化穿越防护：真实根内映射用 `resolve()+relative_to`；逃逸路径（`..`、跨根绝对路径）直接拒绝并返回可读错误；沙箱模式下任何对真实文件系统的写入尝试（绕过 helper 的落盘）在代码评审/测试口径中视为缺陷。
- **FR-11**：上述 4 个有落盘行为的智能体（script_editor/web_search/save_manager/installation）改用统一的沙箱感知文件操作 helper（session 为 None 时等价直接落盘，direct 模式行为不变）；只读智能体（log_analyzer 等）与 dlc/update 无需改造。

### 变更审核与落盘

- **FR-12**：票据全部任务结束后（sandbox 模式），`/chat` 响应携带沙箱票据号与变更清单；WPF 弹出变更审核面板：文件相对路径、变更类型（新建/修改/删除/下载/根外输出）、大小；文本文件可查看新内容（修改类并排展示旧内容），二进制/非文本只展示元信息（大小、扩展名）。
- **FR-13**：后端提供 `GET /sandbox/sessions/{ticket_id}`（详情+清单）、`POST /sandbox/sessions/{ticket_id}/apply`、`POST /sandbox/sessions/{ticket_id}/discard`、`GET /sandbox/pending`（列出 ready 未决会话，供应用重启后找回）。
- **FR-14**：apply 语义：逐个变更项执行——修改/删除真实文件前先用现有备份机制备份到 `~/.gamedoctor/backups/`；overlay 文件覆盖/新建到真实位置；tombstone 项删除真实文件；`side/` 内输出复制回其原始目标位置；逐项记录成功/失败并返回明细。任一项失败不中断其余项，响应汇总失败项与备份位置；会话状态机为 `running → ready → applied | discarded`。
- **FR-15**：discard 语义：删除该票据沙箱目录，真实文件系统零变化；最终回复明确告知变更已丢弃。
- **FR-16**：沙箱模式最终回奏话术必须区分"已在沙箱完成、待审核应用"与"已落盘完成"；未应用前不得向用户声称修改已生效。

## Non-Functional Requirements

- **NFR-1（安全）**：沙箱模式下真实文件系统在 apply 前零写入（自发备份等根外写入也重定向）；该属性须有自动化测试证明（对真实目录做写后快照/断言）。
- **NFR-2（兼容）**：现有全量 133 项测试保持通过（受新默认值影响的测试显式指定 direct 或注入预批准门）；`pytest` 与 `dotnet build` 均通过。
- **NFR-3（可靠）**：审批挂起不占用忙等（asyncio 事件）；后端重启后残留沙箱不丢（meta.json 持久化）、可通过 `/sandbox/pending` 找回应用或丢弃。
- **NFR-4（体量）**：沙箱按需存文件，游戏目录体量不影响沙箱初始化耗时（禁止 copytree 整盘）。
- **NFR-5（可观测）**：授权请求、批准/拒绝/超时、沙箱重定向、apply/discard 均写 tracker 事件流与 server 日志。
- **NFR-6（一致性）**：新模块/接口沿用项目现有风格：中文 docstring、dataclass、`from __future__ import annotations`、Path 语义。

## Constraints

- **Technical**：Python 3.14（系统解释器跑测试）与 conda `gameAgent` 环境双兼容；Windows 文件语义（占用、编码 GBK/UTF-8）；asyncio 上下文需在 `gather` 并行子任务中正确传播（contextvar 在任务创建时拷贝，满足需求）。
- **Business**：不得破坏三省六部状态机合法转移表；审批拒绝只能产生跳过/失败任务结果，不得引发票据非法转移或 HTTP 5xx。
- **Dependencies**：仅标准库 + 现有依赖；WPF 复用现有新拟物样式与弹窗/面板模式，不引第三方 UI 框架。
- **工程约束**：改动前备份到 `.backups/access_sandbox_<时间戳>/`；优先编辑现有文件。

## Assumptions

- 单机单用户桌面场景，同一时刻只有一个操作者响应授权弹窗，待决列表无需鉴权。
- 游戏目录（game_dir）是用户授权的根；agent 常规只读位置（存档/日志候选目录，可能在目录外）读取沿用现状不拦截，仅在授权弹窗"目标"中如实展示。
- 沙箱会话随票据创建；一张票据（一次 /chat）对应一个沙箱会话与一份变更清单。
- 修改类智能体产出的文本文件大小适合在内存中预览（脚本/配置文件，非 GB 级）。
- HTTP 缺省 sandbox、CLI 缺省 direct 是可接受的默认策略差异（通道能力决定）。

## Acceptance Criteria

### AC-1: 访问模式参数全链路贯穿
- **Type**: `rule`
- **Given**: 桌面端选择"沙箱授权"并发送消息
- **When**: 观察 /chat 请求体与后端票据
- **Then**: 请求体含 `access_mode="sandbox"`，票据详情/flow_log 体现沙箱模式；选择"直接访问"时为 `direct` 且无授权请求
- **Pass Condition**: TestClient 断言两种取值都正确到达 orchestrator 执行层；WPF 请求抓包/日志可见字段
- **Evidence**: 新增后端测试 + WPF 代码审查/运行截图

### AC-2: 任务执行前授权门生效
- **Type**: `rule`
- **Given**: sandbox 模式票据进入调度执行，含至少一个写入任务与一个只读任务
- **When**: 任务执行前
- **Then**: 写入任务与只读任务各产生一条 pending 授权请求（写入标注写性质）；批准后执行；拒绝的任务标记"用户拒绝授权"且其下游按 requires 跳过；10 分钟未决自动拒绝；direct 模式零挂起
- **Pass Condition**: 自动化测试覆盖 approve / reject / 超时 / 并行多请求 四种情形且无 5xx、无非法状态转移
- **Evidence**: `tests/` 新增测试输出

### AC-3: 沙箱隔离真实文件系统零写入
- **Type**: `rule`
- **Given**: sandbox 模式下 agent 对授权根内文件执行 create/edit/delete/replace/download，对根外目标执行备份写入
- **When**: 任务执行完毕但用户尚未 apply
- **Then**: 真实游戏目录内容与执行前逐字节一致；变更全部出现在 `~/.gamedoctor/sandbox/<ticket_id>/`（overlay/side）与 meta.json；删除仅记 tombstone
- **Pass Condition**: 测试对真实目录做文件快照（哈希清单），任务前后一致；overlay 中存在对应产物；meta.json 变更条目齐全
- **Evidence**: 沙箱模块测试输出

### AC-4: 路径穿越与越界写入被拒
- **Type**: `rule`
- **Given**: 沙箱会话真实根为某游戏目录
- **When**: agent 请求写入 `../evil`、绝对路径跨根（如 `C:\Windows\...`）等越界目标
- **Then**: 请求被拒绝并返回可读错误，真实位置与沙箱外均无产物，任务返回失败结果而非异常冒泡
- **Pass Condition**: 至少 3 条穿越用例测试通过
- **Evidence**: 沙箱模块测试输出

### AC-5: 读透传与删除遮蔽语义正确
- **Type**: `rule`
- **Given**: overlay 中存在修改文件 A、真实目录存在未触碰文件 B、tombstone 记录文件 C
- **When**: agent 经沙箱读取 A/B/C
- **Then**: A 读到修改后内容、B 透传真实内容、C 视为不存在
- **Pass Condition**: 三条断言测试通过
- **Evidence**: 沙箱模块测试输出

### AC-6: 审核落盘与备份正确
- **Type**: `rule`
- **Given**: 一个 ready 沙箱会话含新建/修改/删除/根外输出四类变更
- **When**: 调用 apply
- **Then**: 被修改/删除的真实文件先出现在 `~/.gamedoctor/backups/`；overlay 内容落到位、tombstone 文件被删、side 输出回到原位置；会话状态 applied，响应含逐项结果；discard 另一会话后真实目录无变化且沙箱目录被清理
- **Pass Condition**: apply 与 discard 两条端到端测试通过（含备份存在性断言）
- **Evidence**: 沙箱模块/API 测试输出

### AC-7: 授权与沙箱 HTTP 接口可用
- **Type**: `rule`
- **Given**: 运行中的后端
- **When**: 调用 approvals 与 sandbox 四组端点
- **Then**: 正常返回 JSON；决定未知/过期 id 返回 4xx 可读错误；`/sandbox/pending` 在后端重启模拟后仍能列出 meta 持久化的 ready 会话
- **Pass Condition**: TestClient 测试全部通过
- **Evidence**: API 测试输出

### AC-8: 直接访问模式行为零回归
- **Type**: `rule`
- **Given**: access_mode=direct 的请求与全部既有测试
- **When**: 执行含写操作的任务与全量测试
- **Then**: 文件直接落盘（与现状逐字节一致）、无沙箱目录创建、无授权挂起；`pytest` 全量通过
- **Pass Condition**: 全量测试通过数 ≥ 改造前基线（133）+ 新增测试
- **Evidence**: pytest 汇总输出

### AC-9: 与方案模式正交
- **Type**: `rule`
- **Given**: mode=plan + access_mode=sandbox
- **When**: 请求含变更意图
- **Then**: 写智能体仍被审议封驳（既有规则不变），沙箱变更清单为空，不弹落盘审核
- **Pass Condition**: 组合矩阵（plan/edit × sandbox/direct 四格）行为测试通过
- **Evidence**: governance 测试输出

### AC-10: 桌面端三段式交互完整可用
- **Type**: `rubric`
- **Dimension**: WPF 交互完整性与清晰度（模式开关 / 执行前授权弹窗 / 落盘审核面板）
- **Scale**: 1-5
- **Anchors**: 1 = 三环节缺一或模式状态混乱；3 = 功能齐全但文案/状态反馈含糊、弹窗阻塞体验差；5 = 开关清晰、授权弹窗信息完整可逐条/全部批准、审核面板能预览新旧内容并明确 apply/discard 后果，全程事件流有迹可循
- **Pass Threshold**: >= 4
- **Evidence**: 真实后端 + WPF 联调操作记录/截图

### AC-11: 改造侵入面与工程质量
- **Type**: `rubric`
- **Dimension**: 实现克制性（统一 helper、单点挂载、agent 改造面最小、中文文档、备份规范）
- **Scale**: 1-5
- **Anchors**: 1 = 各 agent 各写一套沙箱逻辑或猴子补丁全局 IO；3 = 有 helper 但存在绕过路径/重复逻辑；5 = 落盘仅经统一 helper、审批仅在 orchestrator 单点、无绕过路径、docstring 与备份齐备
- **Pass Threshold**: >= 4
- **Evidence**: 独立代码审查（review 阶段）

## Open Questions

- [ ] 授权超时 10 分钟是否合适？（先按 10 分钟常量实现，设置项可后续追加）
- [ ] HTTP 默认 sandbox 会让既有自动化脚本/测试首次遇到审批挂起——按 NFR-2 显式改测试为 direct；是否需要在设置中持久化用户的模式选择？（拟：会话内保持，不新增设置项；如用户要求再持久化）
- [ ] `side/` 根外输出（如存档备份到 `~/.gamedoctor/backups`）apply 时直接写回原位置（备份类产物无需再备份）——如不认可，可改为 apply 时丢弃 side。
- [ ] 修改类文本预览：并排新旧全文即可，还是必须行级 diff？（拟：先全文并排，行级 diff 为加分不阻塞验收）
