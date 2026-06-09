# 记忆系统

## 1. 为什么需要记忆

LLM 每次调用无状态——不记得三周前用户说过"用 tabs 不要用 spaces"。记忆系统跨会话记住用户偏好和项目约定。不是"把聊天记录存起来"——是**把长期信息变成小的、可审计的 Markdown 文件**，在需要时召回最相关的注入上下文。

## 2. 核心概念

### 2.1 文件即数据库

`~/.nanocode/projects/<project-hash>/memory/` 目录下每个 `.md` 文件是一条记忆。`MEMORY.md` 是自动同步的索引。不需要数据库、不需要向量存储、不需要 embedding 模型。

### 2.2 召回流水线

用户消息→本地关键词匹配（substring+keyword+entity+recency 多视角打分）→取 top candidates→LLM side-query 精选（"哪些对当前任务有用？"）→预算打包（5 条/25K token/50KB 单条）→注入上下文（带 freshness warning）。

不是完整语义搜索——是"本地多视角候选+LLM 精选"。成本低、延迟低、行为可解释。代价：本地阶段完全漏掉的记忆，LLM 没机会选中。

### 2.3 记忆生命周期

创建（`save_memory()` 写 .md+同步索引）→访问（`mark_accessed()` 更新 access_count）→衰减（`consolidation` 检查 importance+access_count→标记 archived/superseded）→索引更新。

### 2.4 Freshness warning

每条注入的记忆标注"X 天前保存"。提醒模型"这条可能是过时的"。代码相关事实应重新读文件验证，不盲信记忆。

## 3. 总体设计

```
capabilities/memory/
├── types.py          # MemoryEntry、RelevantMemory、MemorySearchHit
├── store.py          # CRUD + MEMORY.md 索引同步
├── retrieval.py      # 召回 + 格式化注入（rendering 合并）
└── consolidation.py  # 去重、衰减、归档（规则实现，不调 LLM）
```

## 4. 详细设计

**`store.py`**：`save_memory()` 写 `.md` 文件（YAML frontmatter+Markdown body）+同步 MEMORY.md。`list_memories()` 扫描目录解析。`mark_accessed()` 更新 access_count 和 last_accessed_at。

**`retrieval.py`**：`select_relevant_memories()` 完整流水线。`format_memories_for_injection()` 格式化（含 freshness warning）+`build_memory_prompt_section()` 构建给模型的记忆系统说明。`start_memory_prefetch()` 异步启动召回——非阻塞。

**`consolidation.py`**：规则判断 importance+access_count→archived/superseded。不额外调 LLM。

## 5. 设计决策

### 为什么不接向量数据库

零额外依赖、文件可审计、可 git 管理。当前几十条记忆不需要向量搜索。向量是未来选项——不是当前瓶颈。

### 为什么用 LLM 精选而非纯规则

本地打分能找到"包含关键词"的候选。但"这条对当前任务有没有用"是语义判断——LLM 在几 KB 候选列表上精选，成本极低（~100 token），准确度远高于规则。

### 为什么带 freshness warning

三周前的记忆可能过时。代码事实应重新读文件验证。

## 6. 面试考点

**Q: 为什么不接向量数据库？** 零依赖、可审计、当前规模不需要。向量是未来选项。

**Q: 记忆会不会过期？** Freshness warning 提醒时效性。Consolidation 标记长期未访问为 archived。代码事实需重新验证。

## 7. 代码导读

**关键行号**：`store.py` save_memory() + list_memories()、`retrieval.py` select_relevant_memories() + format_memories_for_injection()。
