# 架构对比

## 1. 为什么需要对比

理解 NanoCode 的最佳方式是看它和同类工具在相同问题上做了哪些不同的设计选择。

## 2. 横向对比

| 维度 | NanoCode | Claude Code | Codex CLI | Aider |
|------|:--:|:--:|:--:|:--:|
| Agent 循环 | AgentLoop+Backend 策略 | Agent SDK | Agent Loop+SDK | 单 Agent |
| 子 Agent | Fork-and-Return+并行 | Worktree+Skill | Multi-Agent+CSV | 无 |
| Sandbox | Profile/Backend/Policy+bwrap | 容器级 | OS 级 | 无 |
| 上下文压缩 | Budget→Snip→Compact | 自动 | 自动 | Map/Reduce |
| 记忆 | 文件式+LLM 精选 | 文件式 | 内建 | 无 |
| MCP | Stdio transport | 完整 MCP | 内建 | 社区 |

## 3. 关键差异深度分析

### 与 Claude Code 的差异

NanoCode 的 Agent 是纯状态容器——Claude Code 的 Agent SDK 可能是更紧密的内部实现。NanoCode 是双后端（同时支持 OpenAI），Claude Code 是纯 Anthropic。Sandbox 策略不同——Claude Code 容器级 sandbox 更强但更重，NanoCode 的 bwrap 针对日常 Linux 开发优化。

### 与 Codex CLI 的差异

两者都支持多 Agent 并行，但实现路径不同——Codex CLI 的子 Agent 在 git worktree 中隔离，NanoCode 尚无此机制。Codex CLI 有 CSV 批处理（`spawn_agents_on_csv`），NanoCode 没有。Codex CLI 用 TOML 配置 Agent，NanoCode 用 Markdown frontmatter。Codex CLI 的 prompt caching 策略更精细——有工具排序稳定化的设计，NanoCode 的系统提示词固定但工具列表不稳定。

### NanoCode 的独特设计点

1. Agent 从 Mixin→纯状态容器的架构演进（展示了工程重构能力）
2. 三层压缩流水线（Budget→Snip→Compact 递进，只在必要时付更高成本）
3. Profile/Backend/Policy sandbox 三层分离（借鉴 Codex CLI 思路+简化用户接口）
4. 双后端策略模式（不尝试统一消息格式——两份简单代码优于一层复杂抽象）

## 4. 面试考点

**Q: 和 Claude Code 最大的设计差异？** Agent 纯状态容器 vs Agent SDK 的内部实现。双后端 vs 纯 Anthropic。

**Q: 和 Codex CLI 的 sandbox 思路什么关系？** NanoCode 借鉴了 Codex CLI 的 profile/sandbox/approval 分层思路，但做了自己的实现——bwrap 为 Linux 日常开发优化，Profile 只需 7 个选项。

**Q: 如果重新选择，会做哪些不同设计？** 可能更早实现 git worktree 隔离子 Agent（当前并行子 Agent 有文件冲突风险）。可能早期就加结构化输出（当前子 Agent 返回纯文本，主 Agent 需自己解析）。
