# 智能体系统文档

本文档介绍 Game Doctor 的多智能体系统架构和使用方法。

## 系统架构

### 智能体基类
所有智能体都继承自 `BaseAgent`，提供统一接口：

- `execute(task: AgentTask) -> AgentResult`: 执行任务
- `can_handle(task: AgentTask) -> bool`: 判断是否能处理任务
- `get_capabilities() -> List[str]`: 获取能力列表
- `initialize()`: 初始化智能体

### 智能体编排器
`GameAgentOrchestrator` 负责协调多个智能体的工作：

- 任务分配：根据任务类型和能力分配给合适的智能体
- 结果聚合：收集和处理多个智能体的执行结果
- 工作流管理：支持复杂的多阶段任务流程

## 已实现的智能体

### 1. 兼容性检测智能体 (CompatibilityAgent)
**功能**：检测游戏与系统的兼容性

```python
from gamedoctor.agents.compatibility import CompatibilityAgent

agent = CompatibilityAgent()
task = AgentTask(
    name="compatibility_check",
    data={"game_name": "Grand Theft Auto V"}
)
result = agent.execute(task)
```

### 2. 安装规划智能体 (InstallationAgent)
**功能**：智能规划安装流程

```python
from gamedoctor.agents.installation import InstallationAgent

agent = InstallationAgent({"auto_verify": True})
task = AgentTask(
    name="installation_planning",
    data={"game_name": "Minecraft", "source": "steam"}
)
result = agent.execute(task)
```

## CLI 使用

### 列出所有智能体
```bash
gamedoctor agents
```

### 分析游戏
```bash
# 简单分析
gamedoctor analyze "GTA5"

# 完整分析
gamedoctor analyze "GTA5" --full
```

## 工作流示例

### 创建自定义工作流
```python
from gamedoctor.orchestrator.orchestrator import GameAgentOrchestrator, OrchestrationPlan, OrchestrationStage

# 创建编排器
orchestrator = GameAgentOrchestrator()

# 定义工作流
plan = OrchestrationPlan(
    name="game_installation_workflow",
    description="Complete game installation process",
    stages=[
        # 预安装检查
        OrchestrationStage(
            name="pre_install",
            tasks=[
                AgentTask("compatibility_check"),
                AgentTask("system_check")
            ]
        ),
        # 安装过程
        OrchestrationStage(
            name="installation",
            tasks=[
                AgentTask("download"),
                AgentTask("install")
            ],
            dependencies=["pre_install"]
        ),
        # 安装后验证
        OrchestrationStage(
            name="post_install",
            tasks=[
                AgentTask("verification"),
                AgentTask("optimization")
            ],
            dependencies=["installation"]
        )
    ]
)

# 执行工作流
result = await orchestrator.execute_workflow(plan, context)
```

## 扩展智能体

### 创建自定义智能体
```python
from gamedoctor.agents.base_agent import BaseAgent, AgentTask, AgentResult

class CustomAgent(BaseAgent):
    def __init__(self, config=None):
        super().__init__("custom", config)
        self.capabilities = ["custom_task"]
    
    def _do_initialize(self):
        # 初始化逻辑
        pass
    
    async def execute(self, task: AgentTask) -> AgentResult:
        # 任务执行逻辑
        result = {
            "success": True,
            "data": {"result": "success"}
        }
        return AgentResult(**result)
```

### 注册智能体
```python
from gamedoctor.orchestrator.agent_factory import AgentFactory

AgentFactory.register_agent("custom", CustomAgent)
```

## 配置

每个智能体都有自己的配置：

```python
config = {
    "auto_verify": True,
    "preferred_source": "steam",
    "optimize_settings": True
}

agent = CustomAgent(config)
```

## 错误处理

智能体执行失败时会返回包含错误的 `AgentResult`：

```python
result = agent.execute(task)
if not result.success:
    print(f"错误: {result.message}")
    for error in result.errors:
        print(f"- {error}")
```

## 性能监控

智能体支持性能指标记录：

```python
agent.record_metric("execution_time", 1.5)
agent.record_metric("tasks_completed", 10)
```

## 最佳实践

1. **任务设计**：保持任务原子性，每个智能体专注于特定领域
2. **错误处理**：实现适当的错误处理和回退机制
3. **资源管理**：及时释放资源，避免内存泄漏
4. **日志记录**：记录重要操作和错误信息
5. **测试**：为每个智能体编写单元测试