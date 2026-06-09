# 架构对比

## 1. 为什么需要对比

理解 NanoCode 的设计选择，最好的方式是看它和同类工具在相同问题上做了哪些不同决策。

## 2. 横向对比

| 维度 | NanoCode | Claude Code | Codex CLI | Aider |
|------|:--:|:--:|:--:|:--:|
| Agent 循环 | AgentLoop + Backend 策略 | Agent SDK | Agent Loop + SDK | 单 Agent |
| 子 Agent | Fork-and-Return + 并行 | Worktree + Skill | Multi-Agent + CSV 批处理 | 无 |
| Sandbox | Profile/Backend/Policy + bwrap | 容器级 | OS 级 | 无 |
| 上下文压缩 | Budget→Snip→Compact 三层 | 自动 | 自动 | Map/Reduce |
| 记忆 | 文件式 + LLM 精选 | 文件式 | 内建持久化 | 无 |
| MCP | Stdio transport | 完整 MCP | 内建 | 社区 |

## 3. NanoCode 独特点

1. **Agent 纯状态容器**：从 Mixin 重构为显式组合
2. **双后端策略**：同时支持 Anthropic 和 OpenAI-compatible
3. **三层压缩流水线**：Budget→Snip→Compact 递进，只在必要时付更高成本
4. **Profile/Backend/Policy sandbox**：用户只需理解 Profile

## 4. 面试考点

**Q: 和 Claude Code 最大差异？** Agent 纯状态容器 + 双后端并存。Claude Code 是纯 Anthropic 的。

**Q: 为什么不用 Claude Code 的容器级 sandbox？** 复杂度代价——nanocode 选择更轻的方案。bwrap 覆盖 Linux 日常使用的 90% 场景。
