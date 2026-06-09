# 上下文管理

## 概述

上下文管理回答一个问题：**对话太长时，怎么裁剪消息历史而不丢失关键信息？** nanocode 用三层压缩流水线处理这个问题——从温和到激进，每层有明确的触发阈值。

## 三层压缩流水线

```
利用率 < 50%：不触发任何压缩
    │
利用率 > 50%：Budget — 裁剪超长工具结果到 15K-30K 字符
    │
利用率 > 60%：Snip   — 替换陈旧 read_file 结果为占位符
    │
利用率 > 85%：Compact — 调用模型生成摘要，重置消息历史
    │
空闲 > 5分钟：Microcompact — 清除旧结果给后续回合留余量
```

利用率 = `last_input_token_count / effective_context_window`

### 第 1 层：Budget

**触发条件**：利用率 > 50%

**行为**：裁剪每个工具结果到字符预算（高利用率 15K，中等 30K）。保留头尾，中间替换为 `[... budgeted: N chars truncated ...]`。

**为什么先做 Budget**：超长工具结果（如 `grep_search` 返回 10 万行）是上下文膨胀的头号原因。裁剪是最温和的手段——工具调用（tool_use id）和基本结果仍然可见。

### 第 2 层：Snip

**触发条件**：利用率 > 60%

**行为**：替换陈旧的 `read_file` 结果为 `[Content snipped - re-read if needed]`。保留 tool_use id 和 input（能知道"读过哪个文件"），但删除正文。

**Anthropic 特定优化**：对相同文件多次读取只保留最后一次的正文，之前的全部 snipped。避免模型用旧版本的文件内容做决策。

### 第 3 层：Compact

**触发条件**：利用率 > 85%

**行为**：调用模型生成对话摘要，重置消息历史为 `摘要 + 最后一条用户消息`。然后注入 active skill 上下文，确保 compact 后 skill 指令不丢失。

**为什么最后才 Compact**：compact 自身是一次模型调用（消耗 ~2000 token）。只在真正需要时才做。compact 失败时降级——保留未压缩历史继续对话，不中断会话。

### Microcompact（补充层）

**触发条件**：距上次 API 调用超过 5 分钟

**行为**：清除旧的工具结果（标记为 `[Old result cleared]`）。不依赖利用率——纯粹基于"用户离开了一段时间，旧结果可能已经过时"的判断。

## 消息格式保护

压缩过程中必须保持 Anthropic/OpenAI 的消息格式合法性：

- Anthropic：`tool_use` block 必须有匹配的 `tool_result` block——compact 不能打断这个配对
- OpenAI：`role: tool` 消息必须有对应的 `tool_call_id`——compact 不能留下孤立的 tool 消息

## 上下文预算控制

除了消息压缩，上下文管理还包括**注入控制**——不是所有东西都应该放进上下文：

| 内容 | 预算 |
|------|------|
| CLAUDE.md 总字符数 | 60K |
| 单文件 CLAUDE.md/rules | 20K |
| include 深度 | 5 层 |
| Git status | 2000 字符 |
| 记忆召回 | 5 条 / 25K token / 50KB 单条 |
| 会话记忆预算 | 无上限（但单次最多注入限量的记忆） |

## 与 System Prompt 的分工

| 上下文管理 | 系统提示词 |
|-----------|-----------|
| 决定"对话中塞多少内容" | 决定"模型的核心角色" |
| 压缩历史、控制注入、管理 budget | 固定模板、动态附件渲染 |
| `runtime/compressor.py` | `context/builder.py` |

## 面试考点

**Q: 长对话的 compact 摘要质量不够好怎么办？**

当前 compact 用的是模型自己生成摘要（`max_tokens=2048`）。改进方向：用更轻量的模型做摘要、只摘要工具调用结果而保留所有 user/assistant 文字、或者引入滑动窗口策略让摘要逐步演进而非一次性重置。
