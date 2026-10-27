"""⑥ 方案生成与交叉自检（PRD §4.6 F6）。

- :mod:`generator` —— 方案生成，输出**机器可执行动作序列**（原语 + 参数 + 检查点）
- :mod:`critic` —— 交叉自检（与生成不同的模型）
"""

# 统一导出方案生成器与交叉自检器
from .critic import Critic, LLMCritic, StructuralCritic
from .generator import Solver, LLMSolver

__all__ = ["Solver", "LLMSolver", "Critic", "LLMCritic", "StructuralCritic"]
