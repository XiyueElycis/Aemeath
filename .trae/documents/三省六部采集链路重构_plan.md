# 三省六部采集链路重构 实施计划

> 依据：`三省六部架构优化方案.md`（2026-09-16）+ 本轮对全部相关源码的二次实读核验。
> 原则：制度层（状态机/权限/审议/规划）不动；只接通采集主干、修死代码、让占位诚实。
> 约束：每批改前按用户惯例备份到 `.backups/<feature>_<ts>/`；每批结束跑全量 pytest（当前基线 219 passed）。

## 一、仓库核验结论（文档论断复核）

### 已核实成立

| 论断 | 证据 |
|---|---|
| 治理链路只填 GameContext 2 字段 | [coordinator.py L239-241](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/governance/coordinator.py#L239-L241)，全文件仅此 1 处 `GameContext(` |
| 指纹层仅旧管线调用 | [pipeline.py L18-20](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/pipeline.py) 导入 detect_engine/detect_platform/probe_runtime；governance 包零引用 |
| 任务数据三处构造点 | [planner.py:202-206](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/governance/planner.py)、coordinator.py:402-406（直办）、coordinator.py:551-555（`_execute_agent`） |
| GameContext 10 字段 | [models.py:328-341](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/models.py) |
| system_info 死代码 | `get_system_requirements_check()` 模块级函数体内用 `self.`（L323+）；`get_gpu_info` 用 `.contains()`（L105-148）；`Win32_DirectX` 假 WMI 类（L189-234） |
| 兼容性参数错配 | [compatibility_agent.py:193-217](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/compatibility/compatibility_agent.py) 第 4 参传 `requirements["storage"]`；`_is_cpu_compatible` 恒 True；需求表硬编码 |
| installation 无视 game_dir | [installation_agent.py:59-141](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/installation/installation_agent.py) execute 只读 `game_name`；`_optimize_install_path(game_name, source, sx)` 无 game_dir 参 |
| 知识库无灌库入口 | 全仓 grep `.upsert(` = **0 调用方**；默认空库 |
| 向量导入路径错位 | [retriever.py:171](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/knowledge/retriever.py) `from ...rag import rag_data` 超出顶级包必失败；`rag/rag_data.py` 真实存在于项目顶层 |
| 工厂双轨命名 | [agent_factory.py:197-210](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/orchestrator/agent_factory.py) 键为 audio/social/community/network，而 configs 的 name 与 BaseAgent.name 是 audio_expert 等 |
| pipeline 类型注解缺导入 | [pipeline.py:23-32](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/pipeline.py) 确无 SearchResult/KnowledgeHit/LogEntry |
| 审议官可拿到任务结果 | `execute_workflow` 返回值含 `task_results: Dict[str, AgentResult]`（orchestrator.py:107,138），review_outcome 可据此加 unverified 规则 |
| AgentResult 可扩展 | [base_agent.py:65-87](file:///d:/Python/游戏辅助安装智能体/src/gamedoctor/agents/base_agent.py)，ReviewVerdict 已有 warnings 字段（reviewer.py:31-36） |

### 对文档示例代码的 3 处必要修正（实施时以本计划为准）

1. **指纹数据类形状**：`engine` 是 `EngineFingerprint`（取 `.engine` 得字符串），不是裸字符串；`TechStackFingerprint(game_name, engine, runtime, platform)` 四参；`probe_runtime()` 已带进程内缓存设计空间（当前无缓存，本项目标加）。
2. **知识库 seed 的真实约束**：rag 文档 front-matter **没有 signature 字段**（字段为 title/game/category/tags），错误签名在正文 `# 常见错误日志` 节；`upsert()` 真实签名是 `(signature, tech_stack, repair_template)` 且 **entries 表无 UNIQUE 约束**（重复灌库会产生重复行）。→ seed 需：按「常见错误日志」节+tags+文件名提取**多个签名**；建 `UNIQUE INDEX(signature)`（空库迁移安全）后 INSERT OR REPLACE；跳过 `rag_data.md`、`minecraft.md` 索引文件；tech_stack 列存游戏目录名。
3. **任务数据跨边界序列化**：`task.data` 经 HTTP/JSON 边界流转，**不能**直接塞 dataclass 对象。→ GameContext 挂扁平字段供进程内使用；三处 `data` 里只放 `fingerprint` 的纯 dict（`asdict` 子集，含 engine/gpu/os/directx/dotnet/vc/platform/app_id）。

### 文档数字小误（不影响实施）

- rag 实际：GTA V 12 篇 md、minecraft 22 个 md（含 2 个索引文件，知识文 20 篇）；"33 篇"为约数。
- 行号整体有 ±2 行漂移，实施时以符号名定位。

## 二、批次与改动清单

### 第 1 批：接通采集主干（P0，收益最大、无外部依赖）

- **新增** `src/gamedoctor/context_builder.py`
  - `build_fingerprint(game_name, game_dir) -> TechStackFingerprint`：三段探测各自 try/except 降级，绝不抛异常
  - `build_context(game_name, game_dir) -> tuple[GameContext, dict]`：指纹回填 GameContext 扁平字段（见下），同时返回可 JSON 化的 `fingerprint_dict`
  - `_dir_size_mb()` 带 20000 文件上限
  - `@lru_cache` 缓存 `probe_runtime()` 结果（PowerShell 子进程全请求只起一次）
- `models.py` GameContext 增字段（均有默认值，向后兼容）：
  `engine/gpu_name/gpu_driver/gpu_vram_mb/directx_version/dotnet_version/vc_components/os_name/os_arch/fingerprint_ready`
- 接线点 4 处：
  - coordinator.py:239 改调 `build_context`
  - planner.py:202-206、coordinator.py:402-406、coordinator.py:551-555 三处 data 增 `"fingerprint": fingerprint_dict`
- 平台权威源规则：Steam/Epic appmanifest 识别出的 install_path 优先于手输；识别不到才用 game_dir
- 测试 `tests/test_context_builder.py`：仿真目录断言 engine=unity；CIM 不可用时 monkeypatch probe_runtime；断言 fingerprint_dict 可 `json.dumps`

### 第 2 批：废弃失真采集层（P0）

- `utils/system_info.py`：
  - `get_gpu_info()` 改委托 `SystemRuntimeProbe()._probe_gpu_windows()`（实测存在，返回 (name, driver)），删除 wmi 路径与 `.contains()`
  - `get_os_info()` 改 `platform.release()` + 注册表 `DisplayVersion`（仅作补充），不再读遗留 ProductName/CurrentVersion
  - `get_directx_info()` 改委托 `SystemRuntimeProbe()._probe_directx()`
  - **删除** `get_system_requirements_check()`（含 NameError、全仓无调用方，删前再 grep 一次确认）
- 显存探测新增（fingerprint/runtime.py 或 context_builder 内工具函数）：
  nvidia-smi `memory.total` → 失败回退 `AdapterRAM`（≤4GB 才可信，>4GB 溢出置 None）→ 取不到置 **None 而非 0**
- 测试 `tests/test_system_info.py`：CIM 可用时 GPU 非 Unknown（否则 skip）；断言旧函数名已不存在

### 第 3 批：重写兼容性判定（P0）

- `_is_cpu_compatible`：实现 `_parse_cpu_tier`（解析 i3/i5/i7/i9 + 代号数字），解析不出返回 True（漏报优于误报）
- `_is_gpu_compatible`：参数正名为 `(current_gpu, current_vram_mb, required_gpu, required_vram_mb)`；None 表示"未知/未要求"→ 该项不判不合格
- 调用处：第 4 参改显存需求（不再传 storage）；显存来自第 1 批 GameContext/指纹
- `_check_os_compatibility`：用真实 release/build 比较，弃用 6.3 遗留值
- 需求表：新增 `_REQUIREMENTS_BY_ENGINE`（unity/unreal/renpy/java 档位），指纹引擎命中时取档；硬编码表降为最后兜底
- 数据来源切换：compatibility 与 perf_security 统一从指纹/context_builder 取系统信息，不再各自直调 system_info 全量重采
- 测试 `tests/test_compatibility_truth.py`：注入"RTX4070/16GB/Win11/显存8GB" → 0 critical；注入"Win7/显存 None" → OS 项不合格、GPU 项不报错；CPU 代际比较用例

### 第 4 批：installation 尊重传入目录（P0）

- execute 读 `task_data["game_dir"]`，透传 `_optimize_install_path(..., game_dir=...)`
- 已装游戏（game_dir 存在）原样返回，绝不重算到 `~/Games`
- `_find_main_executable` 三策略：目录同名 exe → 常见固定名（补 play/run）→ 顶层最大 exe（排除 unins/setup/install/redist/vcredist/dxsetup）
- `_check_steam_game/_check_epic_game` 改调 `detect_platform`，探测不到返回 False（不再恒 True），pre_install_checks 注明"未能确认平台归属"
- 测试 `tests/test_installation_dir.py`：传仿真目录 → install_path 等于传入值、找到 `TestGame.exe`、verification.success=True

### 第 5 批：知识库灌库与零配置命中（P0，与第 1 批并列最高收益）

- **新增** `src/gamedoctor/knowledge/seed.py`：
  - `seed_from_rag(rag_root=None) -> dict[str,int]`，根目录默认 `<项目根>/rag`（`Path(__file__).parents[3]`）
  - 解析：front-matter（title/game/category/tags）+ `# 解决方案` 节为 template + `# 常见错误日志` 节每行/tags/文件名为签名；一篇 md 产生多条签名索引
  - 跳过 rag_data.md、minecraft.md 等无「常见错误日志」节的文件
  - 幂等：迁移加 `CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_signature ON entries(signature)`（空库安全；若发现已有重复行先去重），INSERT OR REPLACE
- retriever.py：
  - SQLiteRetriever.search 首次调用且库空时自动 seed 一次（带日志，异常不阻断）
  - `from ...rag import rag_data` 全部改 importlib 按文件路径加载 `rag/rag_data.py`；`langchain_core` ImportError 时 `_check_availability` 记 warning 日志（不静默）
  - 裸 except（L131-132、L147-148、L183-185）补 logging
- `config.py`：注释已声明 rag_data_dir 空值回退项目 rag/——核对回退代码是否真实存在，缺失则在 retriever 内落地该回退
- pyproject.toml：`[project.optional-dependencies]` 的 vector extra 明确 langchain/chromadb（检查现状，缺则补）
- 测试 `tests/test_knowledge_seed.py`：tmp 库 seed 后 `search_knowledge` 对 `UnsatisfiedLinkError`、`d3dx9` 等真实签名非空命中；二次 seed 行数不翻倍（幂等）

### 第 6 批：占位智能体诚实化（P1）

- base_agent.AgentResult 增 `unverified: List[str] = None`（`__post_init__` 初始化为 []）
- audio/social/community/network 的占位返回：空集合替代假数据 + message 明说"能力未接入"+ unverified 申报字段名；network 的端口表按引擎/游戏配置化（无配置返回空+说明）
- reviewer.review_outcome：遍历 outcome["task_results"]，unverified 非空 → warnings 注明"交付须标注待确认，不得作为结论"
- 回奏/汇总层（coordinator 验收后段）将该 warning 带入 thinking/reply
- 测试：4 个占位智能体 unverified 非空且不含伪造设备名；review_outcome 产出对应 warning；现有 test_governance 全绿

### 第 7 批：可接真实数据源（P1，按性价比）

- launch_optimizer：`ctx.engine`/fingerprint_dict 替代游戏名猜引擎与 -dx12 判定（约 3-5 行/处）
- perf_security：显存取第 2 批新探测值，None 时跳过显存瓶颈判定
- audio_expert：`Get-CimInstance Win32_SoundDevice` 真实枚举（与现有 CIM 同源，无新依赖）
- game_content version：Steam 时读 appmanifest 的 BuildID/Version（platform 层已能定位清单）
- social/community：保持第 6 批的诚实申报，不接外部 API
- 测试：各智能体在仿真数据下返回实测值或 unverified，二态必居其一

### 第 8 批：架构一致性收尾（P1/P2）

- agent_factory：`_AGENT_REGISTRY` 12 个键与 DEFAULT_AGENT_CONFIGS 的 name/type、BaseAgent.name 三者对齐为注册名（audio_expert/social_agent/community_agent/network_expert）；同步 register/create 查找链；**新增启动期一致性自检**（不一致直接 raise）+ `tests/test_agent_factory_names.py` 断言 `create_agent("audio_expert")` 可用
- pipeline.py：补 SearchResult/KnowledgeHit/LogEntry 导入；裸 except 全部补 warning 日志
- 文档同步：governance/__init__.py、README「15 → 12」；docs/agents_system.md 补 await 与真实路由名表（执行时再核对行号与文案）
- **链路合并（文档 §4.2）建议后置为独立一轮**：把 DiagnosisPipeline 包成 deep_diagnose 工作流模板涉及 solver/fixer 与治理语义融合，回归面大；本批仅保证 pipeline 可继续独立运行且导入/日志修复，不做合并
- 全量 pytest + 手工仿真目录冒烟（%TEMP% 仿 Unity 游戏跑一轮治理链路）

## 三、实施顺序与依赖

```
批1（接线）─┬─ 批2（system_info）─ 批3（兼容性重写）
           ├─ 批4（installation）
           └─ 批7 的 launch/perf 部分
批5（知识库，无依赖，可与1并行）
批6（诚实化，无依赖）─ 批7 其余
批8（命名/日志/文档，依赖1-7收口）
```

## 四、验证策略

- 每批：全量 `pytest tests -q`（基线 219，只允许数字增长），PYTHONDONTWRITEBYTECODE=1 规避沙箱 pyc 拦截；Python 用 gameAgent 环境并置 PYTHONUTF8=0（editable .pth 兼容）
- 新增测试 6 个：test_context_builder / test_system_info / test_compatibility_truth / test_installation_dir / test_knowledge_seed / test_agent_factory_names，占位申报并入现有治理测试
- 端到端冒烟：仿真游戏目录经 GovernanceCoordinator 跑通，断言 ctx.engine/gpu/platform 非默认、installation 找到同名 exe、知识库命中、占位项带 unverified
- 每批备份改动文件到 `.backups/sansheng_batchN_<ts>/`

## 五、风险与处理

| 风险 | 处理 |
|---|---|
| GameContext 扩字段影响序列化/旧测试 | 全部带默认值；新字段不进 JSON API 的必填位；先跑 test_models/test_orchestrator 验证 |
| CIM/nvidia-smi 在不同机器行为差异 | 探测层一律"取不到置 None + 不抛异常"；判定层 None=不表态；测试用 monkeypatch 注入，不依赖真机 |
| UNIQUE 索引迁移遇已有重复行 | 迁移前 SELECT 查重，存在则按 signature 保留最小 rowid 去重再建索引（当前库实测为空，低风险） |
| 工厂改名影响 HTTP 层按名路由 | 改名后三处对齐 + 启动自检 + 全量测试；保留一个版本的别名映射以防外部调用（自检通过后下轮删） |
| 批 1 把平台识别的 install_path 覆盖手输目录造成困惑 | 仅当 appmanifest 权威命中才覆盖；同时把原始 game_dir 留在 fingerprint_dict 与日志中 |
| 改造面大、单轮交付风险 | 建议分两轮：第一轮批 1-6（全部 P0 + 诚实化），第二轮批 7-8；审批时确认范围 |
