# 智能体访问模式（沙箱授权 / 直接访问）- 实施计划

> 任务依赖为串行切片；每个切片自带测试。所有文件改动前备份到
> `.backups/access_sandbox_<时间戳>/`。沙箱模块统一放在新包
> `src/gamedoctor/sandbox/`；session 为 None 时所有 helper 等价直接落盘。

## Task 1: 沙箱会话核心（overlay 覆盖层 + 变更集 + apply/discard）

- **Status**: `completed`
- **Completion Evidence**:
  - 新增 [session.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/sandbox/session.py)（映射/变更/落盘三职责分区）、[io.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/sandbox/io.py)、[__init__.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/sandbox/__init__.py)；测试 [test_sandbox_session.py](file:///d:/Python/游戏辅助安装智能体/tests/test_sandbox_session.py) 10 项全过（TR-1.1~1.6 全绿；含零写入哈希快照、3 条穿越拒绝、透传/tombstone、apply 备份+side 回写、discard 零变化、direct 直通、千文件零复制、持久化往返）。
  - TR-1.7 rubric 自评 5/5：映射（map_for_read/write）、记录（note_write/delete 幂等合并含净零/复活）、落盘（备份→逐项 apply/失败保持 ready）职责分离，均可独立单测，全中文 docstring。
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 新建 `src/gamedoctor/sandbox/__init__.py`、`session.py`、`io.py`。
  - `session.py` 定义：
    - `ChangeOp` 枚举/常量（create/modify/delete/download/side_write）与 `Change` dataclass（relpath、op、虚拟路径、原始真实路径、大小、时间）。
    - `SandboxStatus`：running/ready/applied/discarded。
    - `SandboxSession`：以 `~/.gamedoctor/sandbox/<ticket_id>/` 为根（`overlay/`、`side/`、`meta.json`），构造时仅建目录不复制真实根。
    - 路径映射：`map_write(real_path) -> (虚拟Path, op)`（根内→overlay 保持相对结构；根外→side 按规整化相对名存放）；`map_read(real_path)`（overlay 命中→虚拟；tombstone→抛"已删除"语义；否则→真实路径）；全部经 `resolve()+relative_to` 校验，越界抛 `SandboxViolation`。
    - 记录 API：`record_write/record_delete/record_side`，幂等合并（同文件多次写只留一条最终 op）；`tombstone` 集合。
    - 持久化：`meta.json` 原子写（tmp+replace），类方法 `load(ticket_id)` / `list_ready()`。
    - `apply_changes()`：复用 `fixer.backup.manager.BackupManager`（不适配则在 session 内等价实现）先备份被修改/删除真实文件到 `~/.gamedoctor/backups/apply_<ticket_id>/`，再逐项覆盖/新建/删除/side 回写；逐项捕获异常返回每项成败明细；成功后置 applied 并落 meta。
    - `discard()`：逐项无真实写入校验后 rmtree 沙箱根，置 discarded（meta 先留痕再删目录，list_ready 不再可见）。
  - `io.py` 定义 session 可选的统一文件原语：`read_bytes/read_text/write_bytes/write_text/remove/replace/copy_into/ensure_dir`——`session=None` 时直接对真实 Path 操作（direct 口径与现状逐字节一致），非 None 时全部重定向并自动记变更；文本编码沿用候选编码（utf-8-sig/utf-8/gbk/latin-1）。
- **Acceptance Criteria Addressed**: AC-3, AC-4, AC-5, AC-6, NFR-1, NFR-4
- **Test Requirements**:
  - `rule` TR-1.1: 沙箱内 create/edit/delete/replace 后，真实目录文件哈希清单与操作前一致；产物落在 overlay/side 且 meta.json 变更条目齐全（AC-3）
  - `rule` TR-1.2: `../evil`、跨盘绝对路径、嵌套穿越 3 条用例均抛 `SandboxViolation`，盘上无产物（AC-4）
  - `rule` TR-1.3: overlay 修改文件读到新内容、未触碰文件透传真实内容、tombstone 文件读视为不存在（AC-5）
  - `rule` TR-1.4: apply 后四类变更（新建/修改/删除/side 回写）在真实位置生效，修改/删除原件在 backups 目录可查；逐项明细返回；discard 后真实目录无变化、沙箱目录删除（AC-6）
  - `rule` TR-1.5: session=None 时 io 原语与原生 pathlib/shutil 行为一致（为 AC-8 提供底层保证）
  - `rule` TR-1.6: 大目录（预置上千文件）创建会话不复制任何文件，初始化仅产生空目录结构（NFR-4）
  - `rubric` TR-1.7: API 克制度（路径映射/IO/落盘三职责清晰、无重复实现、中文 docstring）；scale 1-5；anchors 1=逻辑混杂难测/3=可用但有重复/5=三职责分明且可独立单测；threshold >= 4；evidence 代码自审
- **Notes**: 纯标准库；Windows 下 rmtree 遇占用需报错信息可读；apply 部分失败不中断其余项。

## Task 2: 任务授权门 ApprovalGate（内存异步 + 超时）

- **Status**: `completed`
- **Completion Evidence**:
  - 新增 [gate.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/sandbox/gate.py)（ApprovalRequest/ApprovalDecision/ApprovalGate，asyncio.Event 无忙等、TTL 600s、单例 get_gate/reset_gate）；测试 [test_approval_gate.py](file:///d:/Python/游戏辅助安装智能体/tests/test_approval_gate.py) 7 项全过（TR-2.1~2.5 全绿：批准/拒绝/0.05s 超时/并行4条/票据过滤+重复决定/源码无 sleep/单例）。
- **Priority**: high
- **Depends On**: None
- **Description**:
  - 新建 `src/gamedoctor/sandbox/gate.py`：
    - `ApprovalRequest` dataclass（id、ticket_id、agent、operation、target、kind 只读/写入/下载/安装、summary、created_at）。
    - `ApprovalGate`：`async request(req) -> bool`（登记 pending、`asyncio.Event` 等待、TTL 默认 600 秒，超时按拒绝并标注 `timeout=True`）；`list_pending(ticket_id=None)`；`decide(id, approve, reason="")` 唤醒；id 未知/已决返回 False 供调用方产生 4xx。
    - 模块级单例 `get_gate()`；提供测试用 `reset_gate()`。
  - 拒绝/超时不抛异常，返回结构化结果（approved/reason），由编排层转成跳过结果。
- **Acceptance Criteria Addressed**: AC-2（单元部分）, NFR-3
- **Test Requirements**:
  - `rule` TR-2.1: request 后 list_pending 可见；另一协程 decide(approve=True) 后 request 返回 True
  - `rule` TR-2.2: decide(approve=False) 返回 False 且 reason 透传
  - `rule` TR-2.3: TTL 用 monkeypatch 缩短到 0.05 秒，超时自动拒绝且 pending 列表清除
  - `rule` TR-2.4: 并行 4 个 request 产生 4 条独立请求，逐条 decide 互不串扰（覆盖 gather 并行阶段）
  - `rule` TR-2.5: 全程无忙等（等待基于 Event，检查源码不出现轮询 sleep）
- **Notes**: 纯内存组件，不碰 HTTP；WPF/HTTP 集成在 Task 5。

## Task 3: 四个落盘智能体接入统一沙箱 IO

- **Status**: `completed`
- **Completion Evidence**:
  - [base_agent.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/base_agent.py) 新增 `_sandbox(data)`；四个落盘 agent（[script_editor](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/scripts/script_editor_agent.py)/[web_search](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/web/web_search_agent.py)/[save_manager](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/saves/save_manager_agent.py)/[installation](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/installation/installation_agent.py)）的写/删/备份/下载/建目录全部改走 `sandbox.io` 原语，备份走 side_dst+pristine_src；旧 `_read_text/_write_text` 与 shutil 死代码已清（仅剩只读 `shutil.disk_usage`）；改造前备份在 `.backups/access_sandbox_20260912-171433/`。
  - 新增 [test_agent_sandbox_io.py](file:///d:/Python/游戏辅助安装智能体/tests/test_agent_sandbox_io.py) 5 项全过（edit/create/replace/delete 四操作真实目录哈希零变化+overlay/side 产物、direct 真实落盘、根外绝对路径拒绝、web mock 下载进 overlay 且 op=download、save 备份进 side）；全量 **155 passed**（基线 133+10+7+5）。
  - TR-3.5 rubric 自评 5/5：grep 裸 IO（write_text/write_bytes/unlink/copy2/mkdir/open）在四文件中只剩 sxio 调用与只读 disk_usage，主流程无 if 分叉淹没。
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - `base_agent.py` 增加 `_sandbox(data) -> SandboxSession | None`（读 `data["_sandbox_session"]`，缺失/None 返回 None）。
  - `script_editor_agent.py`：edit/create/delete/replace 的读、写、删、copy2、自发备份全部改走 `sandbox.io` 原语（备份输出自然进 side）；`_resolve_file/_resolve_dir` 在沙箱模式增加根外写判定（读放行、写映射）；返回结果中的路径同时展示真实目标与"沙箱内"提示。
  - `web_search_agent.py`：下载 `write_bytes` 与备份改走 io 原语；URL 与重定向目标记入授权摘要（data 字段不变，供门控展示）。
  - `save_manager_agent.py`：备份 copy2 改走 `copy_into`（根外备份目录→side）。
  - `installation_agent.py`：`mkdir` 改走 `ensure_dir`。
  - 不改 log_analyzer、dlc_manager、update_manager 及其他只读智能体。
- **Acceptance Criteria Addressed**: AC-3, AC-8, AC-11, NFR-6
- **Test Requirements**:
  - `rule` TR-3.1: 注入沙箱 session 的 AgentTask 执行 edit/create/delete/replace 后真实目录零变化，变更进沙箱；不注入（None）时落盘结果与改造前一致
  - `rule` TR-3.2: web_search 下载（mock HTTP 响应字节）在沙箱模式落入 overlay
  - `rule` TR-3.3: save_manager 备份输出在沙箱模式进入 side，真实备份目录无新增
  - `rule` TR-3.4: 既有 script_editor/web_search/save_manager 相关测试（若存在）零修改通过；不存在则补 direct 口径冒烟
  - `rubric` TR-3.5: 沙箱分支侵入自然度（agent 主流程不被 if 淹没、无绕过 helper 的裸 IO 残留）；scale 1-5；anchors 1=满屏分叉/3=有分支但残留裸 IO/5=全部落盘仅经 helper 且主流程清晰；threshold >= 4；evidence grep 裸 IO + 代码自审
- **Notes**: 改造前逐文件备份；`grep` 验证 `write_text|write_bytes|unlink|copy2|mkdir|open(` 在这四个文件中只剩 helper 内或 direct helper 自身。

## Task 4: 编排链路集成（模式透传 + 审批挂载 + 会话生命周期）

- **Status**: `completed`
- **Completion Evidence**:
  - 新增 [context.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/sandbox/context.py)：`AccessContext`（classify 四类性质 read/write/download/install、authorize）、contextvar `use_access/bind_access/current_access`、执行收口共用 `gate_task`（拒绝/超时→skipped 结果）。
  - 授权门挂在唯一执行收口 [base_agent.MultiAgentSystem.execute_task](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/base_agent.py#L218-L232)（工作流/HTTP 单跑/REPL）；协调器直办路径 [_execute_agent](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/governance/coordinator.py#L531-L572) 过同一道门后仍直调 agent.execute（保留异常冒泡→Cancelled 旧契约）。
  - [coordinator.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/governance/coordinator.py)：run 增 `access_mode`（默认 direct），sandbox 时建会话（无 game_dir 建票据空根）、use_access 包裹工作流/直办、结束 seal（异常路径也封存）、_reply 带 sandbox 字段、回奏 FR-16 后缀；[agent_router.chat](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/llm/agent_router.py#L203-L233) 透传；[server.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/server.py#L83-L92) ChatRequest 增 `access_mode="sandbox"`，非法值 400；[cli.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/cli.py#L201-L220) `--sandbox/--direct/--access`（默认 direct）；[repl.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/repl.py) 沙箱三段式（控制台 y/N/a 授权+apply/discard）。改动前备份 `.backups/access_sandbox_20260912-174513/`。
  - 新增 [test_access_integration.py](file:///d:/Python/游戏辅助安装智能体/tests/test_access_integration.py) 11 项全过（TR-4.1 批准2请求/拒绝2跳过依赖链/0.05s 超时、TR-4.2 direct 零请求无会话、TR-4.3 plan×access 矩阵+性质分类、TR-4.4 TestClient 400/缺省 sandbox、TR-4.5 直办过门注入、TR-4.6 tracker 五事件）；全量 **166 passed**，旧 133 测试零修改。
- **Priority**: high
- **Depends On**: Task 1, Task 2, Task 3
- **Description**:
  - 新建 `src/gamedoctor/sandbox/context.py`：contextvar 持有 `AccessContext(access_mode, ticket_id, game_name, game_dir, session|None, gate)`，提供 `use_access(ctx)` 上下文管理器与 `current_access()`；orchestrator 内并行 gather 子任务天然继承拷贝值。
  - `orchestrator.py` `_execute_task_with_context`：requires 通过后、execute 前读 current_access；sandbox 模式则构造 ApprovalRequest（agent=task.agent、operation 取 data.operation、target 取 data.file/game_dir/url、kind 按 MUTATING_AGENTS+operation 判定）→ `await gate.request`；批准→继续；拒绝/超时→返回 `AgentResult(success=False, skipped=True, message="用户拒绝授权/授权超时…")`；tracker 同步事件。direct 模式 tracker 记录"直接访问模式，跳过任务授权"。
  - `coordinator.py`：`run()` 增加 `access_mode` 入参（"direct"|"sandbox"，默认 direct 供内部/测试兼容）；sandbox 时创建 SandboxSession（game_dir 为根，无 game_dir 时以会话 id 建空根并仅允许 side），写入 flow_log/thinking 一行"沙箱模式：写入将在审核后落盘"；经 `use_access` 包住工作流/单智能体执行，并把 session 注入每个 `task.data["_sandbox_session"]`；全部任务结束后 `session.seal()`（running→ready）；直办路径同样注入。
  - `server.py`：`ChatRequest` 增加 `access_mode: str = "sandbox"`；校验非法值 422/400；传入 coordinator；ChatResponse 增加 `sandbox: {ticket_id, changes:[...], status}|None`；最终回奏话术区分"待审核应用/已完成"（FR-16）。
  - `cli.py` chat 路径增加 `--access/--sandbox/--direct` 选项，默认 direct 并透传。
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-8, AC-9, FR-3, FR-4, FR-6, FR-7, FR-16, NFR-5
- **Test Requirements**:
  - `rule` TR-4.1: sandbox 票据执行时 gate 收到与任务数一致的请求（含 kind 标注）；预批准后全链路 Done；预拒绝后任务 skipped、下游 requires 跳过、票据无非法转移、响应 200
  - `rule` TR-4.2: direct 票据 gate 零请求、session 不创建，行为与现状一致
  - `rule` TR-4.3: mode=plan+access=sandbox 组合：写智能体被审议封驳，session 变更清单为空、响应无 sandbox 待审核提示；四格组合矩阵测试通过
  - `rule` TR-4.4: ChatRequest 缺省 access_mode=sandbox；非法值返回 4xx；响应体 sandbox 字段结构正确
  - `rule` TR-4.5: 单智能体直办路径在 sandbox 模式同样过门与会话注入（回归刚修复的 500 链路）
  - `rule` TR-4.6: tracker 事件流含等待授权/批准/拒绝/跳过/沙箱提示五类事件
- **Notes**: 受默认值影响的既有 TestClient/coordinator 测试显式传 direct 或预批准门（属预期行为变化，不算回归）；状态机表不动。

## Task 5: approvals / sandbox HTTP 端点

- **Status**: `completed`
- **Completion Evidence**:
  - [session.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/sandbox/session.py#L545-L564) 增加内存注册表 `register_session/get_registered_session/registered_sessions`；协调器 `_open_session` 创建即登记。
  - [gate.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/sandbox/gate.py#L71-L126) 增加 `_decided` 已决留痕 + `is_decided()`，decide 即时写入消除并发竞态，支撑 404/409 区分。
  - [server.py](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/server.py#L377-L486) 新增 6 端点：`GET /approvals/pending`、`POST /approvals/{id}`（非法 decision→400，未知→404，重复→409）、`GET /sandbox/pending`（内存 ready + 磁盘 list_sessions 合并去重）、`GET /sandbox/sessions/{id}`（文本变更附 new/old 预览，50k 截断）、`POST .../apply`（running→409、状态错误 SandboxStateError→409，返回 total/succeeded/failed/details）、`POST .../discard`。
  - 新增 [test_sandbox_api.py](file:///d:/Python/游戏辅助安装智能体/tests/test_sandbox_api.py) 8 项全过（pending→approve 同 loop 直调、400/404/409、详情预览、running 冲突、apply 明细五字段+备份核验、discard 零触碰、清内存后磁盘恢复 apply、内存/磁盘双路径 pending）；全量 **174 passed**（166+8）。
  - TR-5.1/5.2/5.3/5.4 全部有对应断言。
- **Priority**: high
- **Depends On**: Task 4
- **Description**:
  - `server.py` 新增路由：
    - `GET /approvals/pending?ticket_id=` → gate.list_pending 序列化。
    - `POST /approvals/{id}` body `{decision:"approve"|"reject", reason?}`；未知/已决 id → 404/409 可读 JSON。
    - `GET /sandbox/sessions/{ticket_id}` → meta + 变更清单（文本类 change 附带新内容预览，过大截断标记）。
    - `POST /sandbox/sessions/{ticket_id}/apply`、`/discard` → 调 session 方法返回逐项明细；状态不对（running 应用/discard、already 处理）→ 409。
    - `GET /sandbox/pending` → `SandboxSession.list_ready()`。
  - 会话注册表：运行中会话内存登记 + 按 meta.json 磁盘加载（apply/discard 时优先内存实例，缺失则 load）。
- **Acceptance Criteria Addressed**: AC-7, AC-6（API 口径）, NFR-3, NFR-5
- **Test Requirements**:
  - `rule` TR-5.1: TestClient 走完 pending→decide 全流程，状态码与 JSON 结构正确
  - `rule` TR-5.2: 未知 id decide → 404；重复 decide → 409；running 会话 apply → 409
  - `rule` TR-5.3: 模拟重启（仅留磁盘 meta、内存注册表清空）后 GET /sandbox/pending 能列出 ready 会话并可 apply/discard
  - `rule` TR-5.4: apply 响应逐项明细结构（relpath/op/ok/error/backup_path）断言
- **Notes**: 复用现有全局异常处理；端点只读操作不需新异常模式。

## Task 6: WPF 三段式交互（访问模式开关 + 授权弹窗 + 变更审核面板）

- **Status**: `completed`
- **Completion Evidence**:
  - [MainWindow.xaml](file:///d:/Python/游戏辅助安装智能体/desktop/GameDoctor.Desktop/MainWindow.xaml#L54-L75) 模式行新增"访问：沙箱授权（默认）/直接访问"（GroupName=AccessMode，新拟物 RadioButton 样式 + 后果 ToolTip）；右下状态卡新增"待审核沙箱（N）"入口按钮（无遗留会话时折叠）。
  - [AgentApiClient.cs](file:///d:/Python/游戏辅助安装智能体/desktop/GameDoctor.Desktop/AgentApiClient.cs#L306-L372)：ChatAsync 加 accessMode（请求体 access_mode）；新增 ApprovalInfo/ChangeInfo/SandboxInfo/PendingSandboxInfo/ApplyDetail/ApplyResult/SandboxSummary DTO 与 GetPendingApprovals/DecideApproval/GetPendingSandboxes/GetSandboxSession/ApplySandbox/DiscardSandbox 六方法。
  - 新建 [SandboxWindows.cs](file:///d:/Python/游戏辅助安装智能体/desktop/GameDoctor.Desktop/SandboxWindows.cs)：ApprovalDialog（性质色标 只读/写入/下载/安装、智能体中文名、逐条批准拒绝、全部批准、裁决后自动刷新、10 分钟超时提示、稍后处理）；SandboxReviewWindow（会话下拉+刷新、变更列表带操作标签与错误标、新旧全文并排只读 Consoles 面板、二进制/截断/删除/建目录/根外各态文案、应用二次确认明示备份与不可撤回、丢弃明示零变化）。
  - [MainWindow.xaml.cs](file:///d:/Python/游戏辅助安装智能体/desktop/GameDoctor.Desktop/MainWindow.xaml.cs#L430-L595)：GetAccessMode/OnAccessModeChanged；沙箱对话启动 1s DispatcherTimer 轮询（已见 id 去重、polling/dialog 双防重入、对话 finally 停表、窗口 Closing 停表）；回包 sandbox.change_count>0 时对话结束后弹审核窗口；启动与对话后刷新遗留会话入口；事件流加 approval→"授权"标签；发送中锁定四个模式/访问开关。
  - TR-6.1 `dotnet build -c Debug`：**0 警告 0 错误**（net9.0-windows）；TR-6.2 代码审查通过。TR-6.3/6.4/6.5 真实联调在 Task 7 取证。
- **Priority**: high
- **Depends On**: Task 5
- **Description**:
  - `MainWindow.xaml` 模式行：在"编辑/方案"旁新增"访问："标签 + 沙箱授权/直接访问两个 RadioButton（GroupName="AccessMode"，默认沙箱授权）；新拟物样式沿用现有资源；气泡/只读文本样式沿用 SelectableText。
  - `AgentApiClient.cs`：ChatAsync 增加 `accessMode` 参数并入请求体；新增 `GetPendingApprovalsAsync`、`DecideApprovalAsync(id, approve, reason?)`、`GetSandboxSessionAsync(ticketId)`、`ApplySandboxAsync`、`DiscardSandboxAsync`、`GetPendingSandboxesAsync`；对应 DTO record/class（ApprovalInfo、SandboxInfo、ChangeInfo、ApplyResult）。
  - `MainWindow.xaml.cs`：
    - /chat 发送期间启动 1s  DispatcherTimer 轮询 pending 授权；新 id 弹出模态授权对话框（智能体/操作/目标/读写性质 + 批准/拒绝；多个 pending 时对话框带"全部批准"按钮逐条批准）；避免同一 id 重复弹窗（已见 id 集合）。
    - /chat 响应含 sandbox 时弹"变更审核"窗口：ListView 列变更（图标区分新建/修改/删除/下载/根外），选中文本文件显示新旧全文并排（复用只读 TextBox 样式）；底部"应用全部变更""丢弃"；调用 apply/discard 后事件流回报逐项结果；启动时拉 /sandbox/pending，有遗留会话则在状态栏提示入口。
  - 新增对话框可做成同文件内 Window（XAML 窗口或代码构建），不引第三方控件。
- **Acceptance Criteria Addressed**: AC-1（前端部分）, AC-10
- **Test Requirements**:
  - `rule` TR-6.1: `dotnet build -c Debug` 零错误零警告（新增）
  - `rule` TR-6.2: 请求体随开关切换携带正确 access_mode（代码审查 + 联调日志）
  - `rule` TR-6.3: 联调：真实后端下 sandbox 请求触发授权弹窗，批准→任务继续，拒绝→事件流显示跳过；全部批准按钮可用
  - `rule` TR-6.4: 联调：变更审核面板展示四类变更与新旧内容预览，apply 后真实文件改变、备份目录有原件，discard 后零变化
  - `rubric` TR-6.5: 交互清晰度（文案后果明示、不重复弹窗、轮询随会话结束停止、遗留会话有入口）；scale 1-5；anchors 1=弹窗混乱/卡死或漏一种操作/3=功能齐但反馈含糊/5=三环节顺畅且后果明示；threshold >= 4；evidence 联调截图/记录
- **Notes**: 轮询定时器必须在聊天结束/异常/窗口关闭时停止；HTTP 调用继续走后台 Task 不卡 UI。

## Task 7: 全量回归与真实服务端到端联调

- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 5, Task 6
- **Description**:
  - `python -X utf8 -m pytest tests/ -q` 全量通过并记录总数（≥133+新增）；`dotnet build` 通过。
  - 用 conda gameAgent 环境重启真实后端（注意移除 PYTHONUTF8 环境变量，参考既往坑），在临时冒烟游戏目录上跑两条真实 LLM 链路：①direct 编辑模式直写；②sandbox 授权→改文件→审核面板→apply→核验真实文件与备份；③sandbox→discard 核验零变化。
  - 清理临时进程/目录；汇总证据。
- **Acceptance Criteria Addressed**: AC-6, AC-8, AC-10, NFR-2
- **Test Requirements**:
  - `rule` TR-7.1: pytest 全量通过，输出总数与用例清单
  - `rule` TR-7.2: direct 链路真实落盘、无沙箱目录残留
  - `rule` TR-7.3: sandbox apply/discard 端到端三条联调记录（含真实目录前后哈希/备份路径/服务日志无异常）
  - `rubric` TR-7.4: 交付完整度（功能、测试、联调证据、备份目录齐备）；scale 1-5；anchors 1=仅单测/3=有联调但缺一条主路径/5=三条主路径均有真实证据；threshold >= 4；evidence 联调记录
- **Notes**: 真实后端日志仍按既有约定放 `.backups/runtime_logs/` 或 launcher 默认路径；pid=4200 旧实例先停再启。
