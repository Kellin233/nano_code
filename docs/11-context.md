# 系统提示词工程

## 概述

系统提示词是 Agent 的"角色定义"——它告诉模型自己是谁、能做什么、什么风格。nanocode 的提示词设计核心是**稳定部分与动态部分分离**——稳定部分利于 Anthropic prompt caching，动态部分通过 user context 注入。

## 架构

```
┌─────────────────────────────┐
│     STABLE_SYSTEM_PROMPT    │  ← 固定模板，缓存友好
│  - System（角色和规则）       │
│  - Doing tasks（任务指南）    │
│  - Using your tools（工具使用）│
│  - Output efficiency（效率）  │
├─────────────────────────────┤
│  DYNAMIC_BOUNDARY 分隔标记   │
├─────────────────────────────┤
│     启动上下文（首次注入）     │
│  - 当前日期、工作区、平台      │
│  - CLAUDE.md 项目指令        │
│  - Git 状态快照              │
├─────────────────────────────┤
│     动态附件                  │
│  - Skill 列表                │
│  - Deferred tool 列表        │
│  - MCP tool delta 通知       │
│  - 记忆召回结果               │
└─────────────────────────────┘
```

## 稳定提示词的设计

`STABLE_SYSTEM_PROMPT` 是 `context/builder.py` 中定义的固定模板。它包含四个 section：

**System**：告诉模型它是 Nano Code，一个编程助手。说明工具执行有权限模式，对话会自动压缩。这些是"元指令"——不随任务变化。

**Doing tasks**：关于如何完成任务的约束。"先读后改"、"不要创建不必要的文件"、"不要估算时间"——这些是行为规范。

**Using your tools**：工具使用的优先级。"不要用 run_shell 当 read_file 用"、"可以并行调用独立工具"——这些是对工具使用的具体指导。

**Output efficiency**："直接说重点"、"尝试最简单的方案"——减少 token 浪费。

**为什么要稳定**：Anthropic 的 prompt caching 会缓存 system prompt 前缀。如果每次请求都改 system prompt，缓存永远不命中。稳定模板 + 动态注入的分离开销极小。

## 动态附件

动态内容一律通过 `<system-reminder>` 标签作为 user context 注入，不改 system prompt：

- **启动上下文**：`build_startup_context()` 生成——日期、工作区、平台、Shell、CLAUDE.md 项目指令、Git 状态。首次对话时注入。
- **Skill 列表**：`render_skill_listing_attachment()` 只列出 skill 名称和调用方式，不注入正文（三层披露的第一层）。
- **Deferred tool 列表**：`render_deferred_tools_attachment()` 告知模型有哪些工具可通过 `tool_search` 激活。
- **MCP tool delta**：`render_mcp_delta_attachment()` 在 MCP 服务工具列表变化时通知模型。
- **记忆召回**：`format_memories_for_injection()` 注入 LLM 精选的最相关记忆，附带 freshness warning。

## CLAUDE.md 加载

`context/sources.py` 的 `load_project_instructions()` 扫描 `CLAUDE.md` 文件链：

```
优先级从低到高：
  ~/.claude/CLAUDE.md        # 用户全局
  → 各级目录 CLAUDE.md        # 项目
  → .claude/CLAUDE.md         # 项目配置
  → .claude/rules/*.md        # 按路径匹配的规则
  → CLAUDE.local.md           # 本地覆盖（不提交 Git）
```

支持 `@path/to/file.md` include 语法（最大深度 5 层，总预算 60K 字符）。

## Git 上下文快照

启动时一次性收集——branch、最近 5 个 commit、`git status --short`。**为什么不实时更新**：对话中代码不断变化，实时更新会导致 context 不一致和被压缩的旧信息互相矛盾。一次性快照明确标注 "snapshot from the start of the conversation"。

## 面试考点

**Q: 改什么内容不会让 Anthropic prompt cache 失效？**

任何在 `DYNAMIC_BOUNDARY` 分隔标记**之后**的变化都不会使缓存失效。所以在 user context 中追加附件、修改注入时机、调整 CLAUDE.md 内容——都安全。但修改 `STABLE_SYSTEM_PROMPT` 的任何文字都会让所有缓存 miss。
