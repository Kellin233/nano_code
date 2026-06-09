# 架构对比与下一步

## 与主流 CLI 产品的对比

| 维度 | nanoCode | Claude Code | Codex CLI | Aider |
|------|:--:|:--:|:--:|:--:|
| **Agent 循环** | AgentLoop + Backend 策略 | Agent SDK | Agent Loop + SDK 调度 | 单 Agent 循环 |
| **子 Agent** | Fork-and-Return + 并行编排 | Worktree + Skill 调用 | Multi-Agent + CSV 批处理 | 无（社区讨论中） |
| **Sandbox** | Profile/Backend/Policy 三层 + bwrap 默认 | 容器级沙箱 | OS 级沙箱 | 无内置 sandbox |
| **工具系统** | ToolRegistry + deferred 激活 | 工具定义 + 权限 | 工具注册 + 沙箱 | Coder 方法 |
| **上下文压缩** | Budget → Snip → Compact 三层 | 自动压缩 | 自动压缩 | Map/Reduce 压缩 |
| **记忆系统** | 文件式 + LLM 精选 | 文件式记忆 | 内建持久化 | 无 |
| **MCP** | Stdio transport + 工具注册 | 完整 MCP 支持 | 内建集成 | 社区插件 |

## 当前已知局限

### 1. 无系统级 Plan Mode

当前只有 `plan` 子 Agent 类型，没有全局"先规划再执行"的模式。主 Agent 拿到 plan 输出后是否按计划执行依赖自觉性。

### 2. 无 worktree 隔离

并行子 Agent 修改文件时可能产生冲突。Codex CLI 用 git worktree 隔离子 Agent 的工作区——nanocode 尚无此机制。

### 3. 子 Agent 无持久化

子 Agent 的结果只在当前对话中可用。`/clear` 或 compact 后丢失。可以引入 ArtifactStore 引用机制。

### 4. 自定义 Agent 无热加载

修改 `.md` 文件后需重启才生效。可改为按 mtime 判断缓存过期。

### 5. 无 streaming tool output

长 shell 命令只能等执行完才看到结果。实时 streaming 会显著提升用户体验。

### 6. 无 prompt caching 策略

当前 system prompt 稳定，但工具定义的顺序和内容每次请求可能不同。这会导致 Anthropic 的工具缓存 miss。

## 性能优化方向

| 方向 | 说明 |
|------|------|
| Prompt caching | 固定 tools 排序，tool schema 稳定化以利用 Anthropic cache |
| 工具结果缓存 | 相同参数的 read_file 在短时间内不重复读 |
| 并行工具调用 | 当前支持的并发安全工具扩展（如 web_fetch） |
| 子 Agent 结果缓存 | 相同 prompt 的 explore Agent 短时缓存结果 |

## 后续 Roadmap

| 优先级 | 功能 | 说明 |
|:--:|------|------|
| P0 | SubAgent 并行编排 | 已设计（subagent.md），待实施 |
| P1 | 系统级 Plan Mode | 全局"规划-执行"模式切换 |
| P2 | 子 Agent 失败重试 | Orchestrator 中增加可重试错误判断 |
| P3 | 自定义 Agent 热加载 | mtime 缓存过期策略 |
| P4 | ArtifactStore 引用机制 | 子 Agent 结果持久化引用 |
| P5 | Sandbox 完整实现 | 按 sandbox.md 设计完善 bwrap backend |

## 面试考点

**Q: 如果你是 tech lead，接下来 3 个月的 roadmap 是什么？**

第 1 个月：完成 SubAgent 并行编排 + 超时预算控制（P0）。第 2 个月：实现系统级 Plan Mode 和 sandbox 的 bwrap backend。第 3 个月：补齐自定义 Agent 热加载、子 Agent 结果持久化、prompt caching 策略。
