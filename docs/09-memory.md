# 记忆系统

## 为什么需要记忆

用户上次说"我喜欢用 tabs 不要用 spaces"——三周后 Agent 应该还记得。但 LLM 的每次调用都是无状态的——它不记得上一场对话的内容。

记忆系统让 Agent 跨会话记住用户偏好、项目约定和重要发现。它不是把聊天记录存起来——而是把长期信息变成小的、可审计的 Markdown 文件，在需要时召回最相关的几条注入上下文。

## 核心概念

### 文件即数据库

```
~/.nanocode/projects/<project-hash>/memory/
    ├── MEMORY.md              # 索引文件（自动同步）
    ├── user_preference.md     # 一条记忆 = 一个 .md 文件
    └── project_decision.md
```

每条记忆是带 YAML frontmatter 的 Markdown 文件。不需要数据库、不需要向量存储、不需要 embedding 模型。`MEMORY.md` 是索引——由系统自动更新，用户不要手动改。

### 召回流水线

```
用户消息 → 本地关键词匹配（打分：substring + keyword + entity + recency）
         → 取 top candidates
         → LLM side-query 精选（"在这些候选中，哪些对当前任务有用？"）
         → 预算打包（5 条 / 25K token / 50KB 单条）
         → 注入上下文（带 freshness warning）
```

不是完整语义搜索——是"本地多视角候选 + LLM 精选"。成本低、延迟低、行为可解释。代价：如果本地候选阶段完全漏掉某条记忆，LLM 没机会选中它。

### 记忆生命周期

创建（save_memory）→ 访问（mark_accessed，更新 access_count）→ 衰减（consolidation 检查 importance + access_count，标记 archived/superseded）→ 索引更新。

## 设计决策

### 为什么不接向量数据库

文件系统的方案：零额外依赖、可 git 管理、可用任何编辑器查看修改、不引入 embedding 模型。向量数据库方案更快更准——但引入外部服务依赖。当前项目规模（几十条记忆）不需要。

### 为什么记忆带 freshness warning

三周前的"项目用 React 18"可能已经过时（项目升级到 React 19 了）。每条注入的记忆都标注了时间——模型被提醒"这条是 X 天前的，可能有变化"。代码相关的事实，模型应该重新读文件验证，而不是盲信记忆。

### 为什么用 LLM 精选而非纯规则打分

本地打分能找到"包含关键词"的候选。但"这条记忆对当前任务有没有用"是语义判断——LLM 在几 KB 的候选列表上做精选，成本极低（~100 token），准确度远高于规则。

## 代码走读

**`types.py`**：`MemoryEntry`（一条记忆的完整描述）、`RelevantMemory`（召回结果）、`MemorySearchHit`（打分中间结果）。

**`store.py`**：CRUD 操作。`save_memory()` 写 .md 文件 + 同步 MEMORY.md 索引。`list_memories()` 扫描目录解析所有文件。

**`retrieval.py`**：`select_relevant_memories()` 完整召回流水线。`format_memories_for_injection()` 格式化注入文本（带 freshness warning）。合并了旧的 `rendering.py`——召回和格式化总是同时变更。

**`consolidation.py`**：去重、衰减、归档。当前用规则实现（不额外调 LLM）——检查 importance 和 access_count 判断是否 archived。

## 面试考点

**Q: 为什么不接向量数据库？**

零额外依赖、文件可审计、适合当前规模（几十条）。向量数据库方案适合更大规模——但不是当前瓶颈。

**Q: 记忆会不会过期？**

Freshness warning 提醒模型"这条是 X 天前的"。代码相关事实需要重新读文件验证。Consolidation 会标记长期未访问的记忆为 archived。
