# 记忆系统

## 1. 为什么需要记忆

LLM 每次调用无状态——不记得上一场对话。记忆系统跨会话记住用户偏好和项目约定。**文件即数据库**——每条记忆是带 YAML frontmatter 的 Markdown 文件，不需要向量数据库。

## 2. 核心概念

### 2.1 召回流水线

```
用户消息 → 本地关键词匹配（substring + keyword + entity + recency 打分）
        → LLM side-query 精选（"哪些对当前任务有用？"）
        → 预算打包（5条/25K token/50KB 单条）
        → 注入上下文（带 freshness warning）
```

不是完整语义搜索——是"本地候选 + LLM 精选"。成本低、延迟低、可解释。代价：本地阶段漏掉的，LLM 没机会选中。

### 2.2 记忆生命周期

创建（save_memory）→ 访问（mark_accessed）→ 衰减（consolidation 检查 importance + access_count）→ 索引更新。

## 3. 总体设计

```
capabilities/memory/
├── types.py          # MemoryEntry、RelevantMemory、MemorySearchHit
├── store.py          # CRUD + MEMORY.md 索引同步
├── retrieval.py      # 召回 + 格式化注入（rendering 合并）
└── consolidation.py  # 去重、衰减、归档
```

## 4. 详细设计

**`store.py`**：`save_memory()` 写 .md 文件 + 同步 MEMORY.md 索引。`list_memories()` 扫描目录解析所有文件。

**`retrieval.py`**：`select_relevant_memories()` 完整流水线。`format_memories_for_injection()` 格式化（带 freshness warning）。`start_memory_prefetch()` 异步启动。

**`consolidation.py`**：规则判断 importance + access_count 决定 archived/superseded。不额外调 LLM。

## 5. 设计决策

### 为什么不接向量数据库

零额外依赖、文件可审计、适合当前规模。向量数据库适合更大规模但不是当前瓶颈。

### 为什么带 freshness warning

三周前的记忆可能过时。模型被提醒"这条是 X 天前的"。代码事实需重新读文件验证。

## 6. 面试考点

**Q: 为什么不接向量数据库？** 零依赖、可审计、当前几十条记忆不需要。向量是未来选项不是当前需求。

## 7. 代码导读

**关键代码**：`store.py` save_memory()、`retrieval.py` select_relevant_memories() + format_memories_for_injection()。
