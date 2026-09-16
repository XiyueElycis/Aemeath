# Game Doctor 智能体生态系统

## 系统概览

Game Doctor 已经发展成为一个包含 12 个智能体的完整游戏辅助生态系统。每个智能体专注于特定功能，通过编排器协同工作，提供全方位的游戏管理解决方案。

## 架构设计

### 核心组件

1. **BaseAgent** - 所有智能体的基类
   - 定义统一的接口和行为模式
   - 提供能力协议和任务执行框架
   - 支持性能监控和错误处理

2. **GameAgentOrchestrator** - 智能体编排器
   - 管理和协调多个智能体
   - 支持复杂的多阶段工作流
   - 提供任务分配和结果聚合

3. **AgentFactory** - 智能体工厂
   - 统一的智能体创建和注册
   - 支持配置驱动的实例化
   - 提供智能体信息查询

4. **AgentTask & AgentResult** - 任务定义和结果结构
   - 标准化的任务格式
   - 统一的结果返回格式
   - 支持优先级和能力要求

## 实现的智能体

> 共 12 个注册智能体（由早期 15 个精简合并：性能+安全合为 `perf_security`，
> 版本/存档/DLC 合为 `game_content`，合并体按任务 `op` 字段分流）。

### 1. 兼容性检测智能体 (CompatibilityAgent)
- **功能**：检测游戏与系统、硬件的兼容性
- **能力**：compatibility_check, system_analysis, hardware_detection
- **输出**：兼容性评分、系统要求检查、已知问题警告

### 2. 安装规划智能体 (InstallationAgent)
- **功能**：智能规划安装流程
- **能力**：installation_planning, path_optimization, source_selection
- **特性**：
  - 自动选择最佳安装源（Steam/Epic/独立）
  - 优化安装路径
  - 磁盘空间检查
  - 安装后验证

### 3. 游戏内容管理智能体 (GameContentAgent)
- **功能**：版本 / 存档 / DLC·Mod 生命周期（三者同挂一条版本基线）
- **能力**：version_check, risk_assessment, rollback；save_backup, cloud_sync,
  save_migration；dlc_management, mod_installation, conflict_detection, version_sync
- **分流**：`op=version`（版本对比/更新风险/回滚）、`op=saves`（存档清点与写前备份）、
  `op=dlc`（DLC/Mod 扫描与冲突检测）；缺省三项全做

### 4. 启动优化智能体 (LaunchOptimizerAgent)
- **功能**：优化启动性能
- **能力**：launch_optimization, background_management, preload_strategy

### 5. 社区资源聚合智能体 (CommunityAgent)
- **功能**：整合社区资源
- **能力**：tutorial_aggregation, content_recommendation

### 6. 游戏社交管理智能体 (SocialAgent)
- **功能**：社交功能集成
- **能力**：friend_status, voice_chat, social_account

### 7. 性能安全智能体 (PerfSecurityAgent)
- **功能**：运行体检——性能瓶颈与反作弊/恶意进程/隐私一次巡检
- **能力**：fps_monitoring, resource_tracking, bottleneck_identification；
  anti_cheat_check（EAC/BattlEye/Vanguard/Ricochet 双路识别）, malware_detection
- **分流**：`op=performance`（仅性能）、`op=security`（仅安全）；缺省两项全检

### 8. 音频专家智能体 (AudioExpertAgent)
- **功能**：音频问题诊断与优化
- **能力**：audio_detection, format_compatibility

### 9. 网络专家智能体 (NetworkExpertAgent)
- **功能**：网络连接优化
- **能力**：ping_test, port_config, server_recommendation

### 10. 日志分析智能体 (LogAnalyzerAgent)
- **功能**：游戏日志定位、错误级别粗分与关键行提取
- **能力**：log_analysis, error_detection, crash_analysis, file_read

### 11. 脚本编辑智能体 (ScriptEditorAgent)
- **功能**：配置/脚本安全编辑（自动备份、锚点替换；沙箱模式下写入重定向 overlay）
- **能力**：script_edit, file_read, file_write, file_create, file_delete

### 12. 联网搜索智能体 (WebSearchAgent)
- **功能**：联网检索与文件下载（仅在「联网搜索」开关开启时注册）
- **能力**：web_search, online_search, file_download

## 已集成功能

### RAG 系统
- 已实现：SQLite + 向量检索的混合知识库
- 支持语义搜索和精确匹配
- 自动索引和回退机制

### CLI 命令
- `gamedoctor agents` - 列出所有智能体
- `gamedoctor analyze <game>` - 分析游戏
- `gamedoctor rag-index` - 管理 RAG 索引
- `gamedoctor config check-deps` - 检查依赖

### 工作流系统
- 支持多阶段任务执行
- 智能任务分配
- 结果聚合和错误处理

## 扩展指南

### 创建新智能体

```python
from gamedoctor.agents.base_agent import BaseAgent, AgentTask, AgentResult

class NewAgent(BaseAgent):
    def __init__(self, config=None):
        super().__init__("new_agent", config)
        self.capabilities = ["new_capability"]
    
    def _do_initialize(self):
        # 初始化逻辑
        pass
    
    async def execute(self, task: AgentTask) -> AgentResult:
        # 执行逻辑
        return AgentResult(success=True, data={})
```

### 注册智能体

```python
from gamedoctor.orchestrator.agent_factory import AgentFactory

AgentFactory.register_agent("new_agent", NewAgent)
```

### 创建工作流

```python
from gamedoctor.orchestrator.orchestrator import GameAgentOrchestrator, OrchestrationPlan

plan = OrchestrationPlan(
    name="custom_workflow",
    stages=[...]
)

result = await orchestrator.execute_workflow(plan)
```

## 性能特性

1. **延迟加载**：智能体按需初始化
2. **并行执行**：支持多任务并行处理
3. **缓存机制**：结果缓存避免重复计算
4. **优雅降级**：依赖缺失时的自动回退

## 错误处理

- 所有智能体都实现错误处理
- 支持部分失败不影响整体流程
- 详细的错误日志和指标记录

## 未来规划

1. **智能体深化**：为现有智能体接入真实平台 API（更新日志、云存档、Mod 源）
2. **AI 增强**：集成更多 AI 能力
3. **插件系统**：支持第三方智能体
4. **Web 界面**：图形化用户界面
5. **云服务**：云端智能体编排

## 使用示例

### 基本使用

```bash
# 列出智能体
gamedoctor agents

# 分析游戏
gamedoctor analyze "Grand Theft Auto V"

# 完整分析
gamedoctor analyze "GTA5" --full
```

### 编程接口

```python
from gamedoctor.orchestrator.agent_factory import AgentManager
from gamedoctor.orchestrator.base_agent import AgentTask

# 创建管理器
manager = AgentManager()
manager.register_agents(configs)

# 执行任务
task = AgentTask("compatibility_check", data={"game_name": "GTA5"})
result = manager.orchestrator.execute_task(task)
```

这个智能体生态系统为 Game Doctor 提供了强大的扩展能力，可以根据需要添加更多智能体，形成一个完整的游戏管理平台。