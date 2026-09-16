# RAG 功能集成文档

本文档说明 Game Doctor 如何集成 RAG（Retrieval-Augmented Generation）功能。

## 概述

Game Doctor 支持两种知识源：
1. **SQLite 知识库**：基于错误签名的精确/模糊匹配
2. **RAG 知识库**：基于语义相似度的向量检索

通过配置可以选择启用哪种知识源，或同时使用两种。

## 配置

在 `~/.gamedoctor/config.toml` 中配置 RAG 相关选项：

```toml
[knowledge]
# SQLite 路径，空则默认 ~/.gamedoctor/knowledge.db
db_path = ""

# 是否启用向量检索（chromadb）
use_vector = true

# RAG 数据目录，空则默认项目 rag/ 目录
rag_data_dir = ""

# 是否自动索引未索引的游戏
auto_index = true

# 嵌入模型
embedding_model = "BAAI/bge-small-zh-v1.5"
```

## 数据准备

### 1. 安装依赖

```bash
# 安装向量数据库依赖
pip install 'gamedoctor[vector]'

# 或手动安装
pip install chromadb>=0.4
```

### 2. 准备游戏知识

在 `rag/` 目录下为每个游戏创建子目录，放入 Markdown 文档：

```
rag/
├── Grand_Theft_Auto_V/
│   ├── asi_loader_missing.md
│   └── mod_install_crash.md
├── Minecraft/
│   ├── java_crash.md
│   └── shader_error.md
└── rag_data.py
```

Markdown 文档格式示例：

```markdown
---
title: "问题描述"
game: "游戏名"
category: "错误分类"
tags: ["标签1", "标签2"]
---

# 问题描述

详细描述问题...

# 解决方案

1. 步骤一
2. 步骤二
```

## 使用方法

### 1. 检查依赖状态

```bash
gamedoctor config check-deps
```

### 2. 查看索引状态

```bash
gamedoctor rag-index --status
```

### 3. 索引游戏知识

```bash
# 索引所有游戏
gamedoctor rag-index

# 索引指定游戏
gamedoctor rag-index --game "Grand_Theft_Auto_V"

# 强制重建索引
gamedoctor rag-index --force
```

### 4. 进行诊断

正常使用诊断命令，系统会自动使用配置的知识源：

```bash
# 诊断游戏问题
gamedoctor diagnose "GTA5" --error "0xc000007b"

# 带自动修复
gamedoctor diagnose "GTA5" --error "0xc000007b" --fix
```

## 工作流程

### 自动索引流程

1. 当 `auto_index = true` 时，在检索前会自动检查并索引游戏
2. 如果游戏中没有向量索引，会自动创建
3. 索引失败不会影响后续的 SQLite 检索

### 检索流程

1. **SQLite 检索**：始终执行，基于错误签名
2. **向量检索**：当 `use_vector = true` 时执行
   - 基于语义相似度搜索
   - 检索结果转换为 KnowledgeHit 格式
3. **结果融合**：
   - 去重（优先保留 SQLite 结果）
   - 按相关性排序
   - 限制结果数量

### 回退机制

- 向量检索失败时，自动回退到 SQLite 检索
- 依赖缺失时，自动禁用向量检索
- 索引失败时，继续使用 SQLite 检索

## API 使用

### 直接使用 RAG 检索器

```python
from gamedoctor.knowledge.retriever import HybridRetriever
from gamedoctor.config import load

# 创建混合检索器
cfg = load()
retriever = HybridRetriever(cfg)

# 执行检索
results = retriever.search("0xc000007b", "启动报错")

# 使用结果
for hit in results:
    print(f"相关性: {hit.score}")
    print(f"解决方案: {hit.repair_template}")
```

### 程序化索引

```python
from gamedoctor.knowledge.retriever import check_and_index_games

# 索引指定游戏
indexed = check_and_index_games(["Grand_Theault_Auto_V"])
print(f"索引结果: {indexed}")

# 索引所有游戏
all_indexed = check_and_index_games()
print(f"所有游戏索引: {all_indexed}")
```

## 故障排除

### 1. ChromaDB 初始化失败

```bash
# 检查依赖
pip install chromadb>=0.4

# 检查数据目录权限
ls -la rag/
```

### 2. 模型加载失败

```bash
# 下载中文 BGE 模型
from langchain_community.embeddings import HuggingFaceBgeEmbeddings
embeddings = HuggingFaceBgeEmbeddings(model_name="BAAI/bge-small-zh-v1.5")
```

### 3. 文档格式错误

确保每个 Markdown 文档：
- 使用 UTF-8 编码
- 文档名使用英文或拼音
- 每个游戏有独立的目录

### 4. 检索结果为空

- 检查游戏目录是否有 .md 文件
- 运行 `gamedoctor rag-index` 重新索引
- 查看配置是否正确设置

## 性能优化

1. **自动索引**：避免每次检索都重新构建索引
2. **结果缓存**：避免重复检索相同内容
3. **混合检索**：平衡准确性和召回率

## 扩展功能

### 自定义适配器

继承 `GameKnowledgeAdapter` 实现自定义知识源：

```python
from gamedoctor.knowledge.rag_data import GameKnowledgeAdapter

class CustomAdapter(GameKnowledgeAdapter):
    def index(self, force=False):
        # 实现索引逻辑
        pass
    
    def search(self, query, k=4):
        # 实现检索逻辑
        pass
```

### 自定义嵌入模型

在配置中指定不同的嵌入模型：

```toml
[knowledge]
embedding_model = "BAAI/bge-large-zh-v1.5"
# 或使用其他模型
# embedding_model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
```

## 最佳实践

1. **文档质量**：
   - 问题描述清晰具体
   - 解决方案步骤详细
   - 包含相关文件路径

2. **分类管理**：
   - 按游戏分类文档
   - 同一问题的不同解决方案分开放置
   - 定期更新和维护文档

3. **索引策略**：
   - 新增游戏后执行索引
   - 文档更新后可强制重建索引
   - 监控索引效果和检索质量