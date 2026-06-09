# 下一步

## 已知局限

1. **无系统级 Plan Mode**：当前只有 `plan` 子 Agent 类型，没有全局"先规划再执行"模式。主 Agent 拿到 plan 输出后是否按计划执行依赖自觉性。

2. **自定义 Agent 无热加载**：修改 `.md` 文件后需重启才生效。可改为按 mtime 判断缓存过期。

3. **子 Agent 结果无持久化**：`/clear` 或 compact 后子 Agent 的执行结果丢失。可引入 ArtifactStore 引用机制。

4. **无 prompt caching 策略**：工具定义的顺序和内容每次请求可能不同，导致 Anthropic 工具缓存 miss。

5. **BwrapBackend 依赖外部工具**：Linux 默认沙箱需要系统安装 `bubblewrap`。未安装时需显式 fallback 到 local。

6. **MCP 只支持 stdio transport**：http/sse/ws 配置可解析但不连接。

## 与主流 CLI 产品对比

| 维度 | nanocode | Claude Code | Codex CLI | Aider |
|------|:--:|:--:|:--:|:--:|
| Agent 循环 | AgentLoop + Backend 策略 | Agent SDK | Agent Loop + SDK | 单 Agent |
| 子 Agent | Fork-and-Return + 并行编排 | Worktree + Skill | Multi-Agent + CSV 批处理 | 无 |
| Sandbox | Profile/Backend/Policy + bwrap 默认 | 容器级 | OS 级 | 无内置 |
| 工具系统 | ToolRegistry + deferred | 工具定义 + 权限 | 工具注册 + 沙箱 | Coder 方法 |
| 上下文压缩 | Budget→Snip→Compact 三层 | 自动压缩 | 自动压缩 | Map/Reduce |
| 记忆 | 文件式 + LLM 精选 | 文件式记忆 | 内建持久化 | 无 |

## Roadmap

| 优先级 | 功能 | 说明 |
|:--:|------|------|
| P0 | Sandbox bwrap backend 完善 | 按 sandbox 设计文档完善 protected paths、env forwarding |
| P1 | 系统级 Plan Mode | 全局"规划-执行"模式切换 |
| P2 | 子 Agent 失败自动重试 | Orchestrator 中判断可重试错误 |
| P3 | 自定义 Agent 热加载 | mtime 缓存过期 |
| P4 | 子 Agent 结果持久化 | ArtifactStore 引用 |
| P5 | prompt caching 策略 | 工具排序稳定化 |
| P6 | MCP 远程 transport | http/sse/ws |
