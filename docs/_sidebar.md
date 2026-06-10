* [引言](00-introduction.md)
  * 架构原则、四层依赖、请求流

* [1. Agent Core 与 Harness](01-runtime.md)
  * Agent 纯内核、AgentLoop、harness、AgentSession 边界

* [2. Providers 模型后端](02-backend.md)
  * Backend 接口、Anthropic/OpenAI 策略类

* [3. 工具系统](03-tools.md)
  * ToolRegistry、ToolRuntime、12 个内置工具

* [4. 权限与安全](04-permissions.md)
  * 四种权限模式、检查顺序、与 sandbox 的关系

* [5. Shell 沙箱](05-sandbox.md)
  * Profile/Backend/Policy、bwrap/microsandbox/local

* [6. 子 Agent 与计划模式](06-subagents.md)
  * Fork-and-Return、SubAgentOrchestrator、plan/explore/general

* [7. 技能系统](07-skills.md)
  * 三层阶段式披露、inline vs fork、参数渲染

* [8. Hooks 生命周期](08-hooks.md)
  * UserPromptSubmit、PreToolUse、PostToolUse、Stop、PreCompact

* [9. 记忆系统](09-memory.md)
  * 文件式存储、本地匹配+LLM 精选、MemoryRuntime

* [10. MCP 集成](10-mcp.md)
  * stdio transport、工具注册、资源操作

* [11. 上下文管理](11-context.md)
  * system prompt、动态附件、五层压缩

* [12. CLI / TUI / Server / 会话](12-cli-tui-session.md)
  * AgentSession、RuntimeThread、事件流、会话持久化

* [13. 架构对比](13-architecture.md)
  * 与 Claude Code / Codex CLI / Aider 对比

* [14. Roadmap](14-roadmap.md)
  * 已知局限、后续改进计划

* [15. 测试指南](15-testing.md)
  * 测试结构、Mock 策略、架构边界检查

* [16. 代码导读](16-code-guide.md)
  * 推荐阅读顺序、关键文件、修改路径

* [17. 扩展系统](17-extensions.md)
  * ExtensionAPI、loader、runner、Hook vs Extension
