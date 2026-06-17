# 下一步

## 已知局限

1. **无系统级 Plan Mode**：当前只有 `plan` 子 Agent 类型，没有全局“先规划、确认后执行”的模式。

2. **子 Agent 无 workspace 隔离**：并行子 Agent 共享同一个工作区。只读探索安全，写文件任务仍可能冲突。

3. **自定义 Agent 无热加载**：修改 `.claude/agents/*.md` 后通常需要重启或清缓存。

4. **Extension 缺少权限隔离**：扩展是进程内 Python，能力强但需要用户信任。未来可增加启用列表、签名或更细粒度权限。

5. **MCP 只支持 stdio transport**：HTTP/SSE/WS 仍是后续工作。

6. **Prompt cache 策略仍可加强**：工具定义排序、deferred 工具稳定性、动态附件边界还可以继续优化。

7. **测试目录名仍沿用历史口径**：`test/runtime`、`test/capabilities` 等目录名保留历史分层名，但当前测试 import 已经指向 `nanocode.agent.*`、`nanocode.agent.harness.*`、`nanocode.cli.core.*` 等新包路径。

## Roadmap

| 优先级 | 功能 | 说明 |
|:--:|------|------|
| P0 | 文档和测试目录跟随新架构收敛 | 文档需持续以当前 `src/` 和 fixture 为准，测试目录可后续重命名 |
| P1 | 系统级 Plan Mode | 全局规划/确认/执行模式 |
| P2 | 子 Agent workspace 隔离 | git worktree 或临时 workspace |
| P3 | Extension 启用策略 | allowlist、错误隔离、诊断输出 |
| P4 | 自定义 Agent 和 Skill 热加载 | 基于 mtime 的缓存失效 |
| P5 | 子 Agent 结果持久化 | ArtifactStore 引用和 resume 支持 |
| P6 | MCP 远程 transport | HTTP/SSE/WS |
| P7 | Prompt cache 稳定化 | 工具排序、动态附件分层、schema cache 策略 |
