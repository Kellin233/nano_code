# 记忆系统

## 1. 为什么需要记忆

LLM 每次调用本身无状态。记忆系统把跨会话长期信息保存成可审计的 Markdown 文件，并在相关任务中召回注入上下文。

记忆是应用层能力，位于 `cli/core/memory/`。Agent core 不直接搜索记忆；`AgentSession` 创建 `MemoryRuntime` 并把 prefetch/consume 回调绑定给 Agent。

## 2. 文件结构

```
cli/core/memory/
├── __init__.py
├── types.py          # MemoryEntry、RelevantMemory、MemorySearchHit
├── store.py          # CRUD + MEMORY.md 索引同步
├── retrieval.py      # 本地候选召回 + 格式化注入
├── consolidation.py  # 去重、衰减、归档
└── runtime.py        # MemoryRuntime，召回编排
```

`runtime.py` 是本轮架构拆分后的新增边界：它把“什么时候召回、怎么调用 side-query、怎么把结果注入 Agent”从 Agent core 中拆出来。

## 3. 存储模型

```
~/.nanocode/projects/<project-hash>/memory/
├── MEMORY.md
├── 2026-xx-xx-user-pref.md
└── ...
```

每条记忆是一个 `.md` 文件，包含 YAML frontmatter 和正文。`MEMORY.md` 是同步索引。当前不依赖数据库、向量存储或 embedding 模型。

## 4. 召回流水线

```
用户 prompt
  → MemoryRuntime.start_prefetch(prompt)
  → retrieval 本地多视角打分
  → LLM side-query 精选候选
  → 预算打包
  → Agent.append_user_context(...)
```

本地阶段负责快速召回候选，side-query 只在少量候选上判断“这条对当前任务是否有用”。side-query callable 由 `AgentSession` 从 provider/backend 注入，memory 模块不 import provider。

## 5. Freshness warning

注入的每条记忆会标注保存时间或相对新旧。模型应把记忆当作线索，而不是代码事实。代码相关事实仍应读文件验证。

## 6. 设计决策

### 为什么不用向量数据库

当前记忆规模通常是几十条，文件式存储足够。它零依赖、可审计、可手工修改，也能被 git 管理。

### 为什么 MemoryRuntime 在 cli/core

召回编排需要调用 side-query LLM、访问 Agent 注入上下文、考虑主/子 Agent 差异。这是应用装配行为，不属于 Agent core。

### 为什么用 LLM 精选

本地关键词召回可解释但不够语义化。LLM 在少量候选上做精选，成本低，效果比纯规则好。

## 7. 代码导读

```
cli/core/memory/store.py
cli/core/memory/retrieval.py
cli/core/memory/runtime.py
cli/session.py::_build_side_query
```
