# 架构对比

## 与主流 CLI 编程 Agent 的对比

| 维度 | NanoCode | Claude Code | Codex CLI | Aider |
|------|:--:|:--:|:--:|:--:|
| Agent 循环 | AgentLoop + Backend 策略 | Agent SDK | Agent Loop + SDK | 单 Agent |
| 子 Agent | Fork-and-Return + 并行编排 | Worktree + Skill | Multi-Agent + CSV 批处理 | 无 |
| Sandbox | Profile/Backend/Policy + bwrap | 容器级 | OS 级 | 无 |
| 上下文压缩 | Budget→Snip→Compact 三层 | 自动压缩 | 自动压缩 | Map/Reduce |
| 记忆 | 文件式 + LLM 精选 | 文件式 | 内建持久化 | 无 |
| MCP | Stdio transport | 完整 MCP | 内建集成 | 社区插件 |
| 权限 | 四层检查 + deny 不可绕过 | 权限模式 | 沙箱 + 审批 | 确认模式 |

## 与 Claude Code 的关键差异

NanoCode 的 Agent 循环和工具系统借鉴了 Claude Code 的设计思想（Fork-and-Return、ToolRegistry、deferred 工具），但在几个点上做了不同选择：

- **双后端并存**：Claude Code 是纯 Anthropic 的。NanoCode 同时支持 OpenAI-compatible 后端，双消息历史分开存储而非统一抽象。
- **Sandbox 更轻**：Claude Code 的 sandbox 是容器级的，所有操作在容器内。NanoCode 的 bwrap 只覆盖 run_shell，文件工具在宿主机执行。
- **没有 Worktree 隔离**：Claude Code 的子 Agent 在独立 git worktree 中运行。NanoCode 还没做这个——并行子 Agent 会有文件冲突风险。

## 与 Codex CLI 的关键差异

- **配置方式**：Codex CLI 用 TOML 文件定义子 Agent，NanoCode 用 Markdown frontmatter。
- **CSV 批处理**：Codex CLI 有 `spawn_agents_on_csv`，NanoCode 没有。
- **Prompt caching 策略**：Codex CLI 的文档强调了缓存友好的工具排序和 prompt 前缀设计。NanoCode 的系统提示词是固定的但工具列表不稳定。

## NanoCode 的独特设计

1. **Profile/Backend/Policy 三层 sandbox**：用户只需理解 Profile，不需要记住 bwrap 参数。这是对 Codex CLI sandbox 思路的借鉴 + 简化。
2. **三层压缩流水线**：Budget → Snip → Compact 的递进策略，比直接用模型生成摘要更节省 API 成本。
3. **Agent 从 Mixin 改为纯状态容器**：这是架构演进的结果，展示了从隐式耦合到显式组合的工程决策。

## 面试考点

**Q: 和 Claude Code 最大的架构差异是什么？**

NanoCode 的 Agent 是纯状态容器——所有行为外移。Claude Code 的 Agent 可能是更紧密的内部实现。NanoCode 的双后端设计也是一个显著差异——不是所有 Agent 都支持 OpenAI-compatible 后端。
