* [引言](00-introduction.md)
  * 项目定位、架构全景图、模块速览

* [1. Runtime 内核](01-runtime.md)
  * Agent 纯状态容器、AgentLoop 主循环、Compressor 压缩

* [2. Backend 模型后端](02-backend.md)
  * Backend 接口、AnthropicBackend/OpenAIBackend 策略类

* [3. 工具系统](03-tools.md)
  * ToolRegistry、ToolRuntime 执行管线、12 个内置工具

* [4. 权限与安全](04-permissions.md)
  * 四种权限模式、四层检查顺序、与 sandbox 的关系

* [5. Shell 沙箱](05-sandbox.md)
  * Profile/Backend/Policy 三层、bwrap/microsandbox/local

* [6. 子 Agent 与计划模式](06-subagents.md)
  * Fork-and-Return、SubAgentOrchestrator 并行编排、plan/explore/general

* [7. 技能系统](07-skills.md)
  * 三层阶段式披露、inline vs fork、参数渲染

* [8. Hooks 生命周期](08-hooks.md)
  * PreToolUse/PostToolUse/Stop、modify 重校验

* [9. 记忆系统](09-memory.md)
  * 文件式存储、本地匹配+LLM 精选、freshness warning

* [10. MCP 集成](10-mcp.md)
  * Stdio transport、工具注册、资源操作

* [11. 上下文管理](11-context.md)
  * System prompt 工程、动态附件、CLAUDE.md 加载

* [12. CLI / TUI / 会话](12-cli-tui-session.md)
  * 参数解析、三种运行模式、会话持久化

* [13. 架构对比](13-architecture.md)
  * 与 Claude Code / Codex CLI / Aider 对比

* [14. Roadmap](14-roadmap.md)
  * 已知局限、后续改进计划

* [15. 测试指南](15-testing.md)
  * 测试结构、Mock 策略、Checklist

* [16. 代码导读](16-code-guide.md)
  * 推荐阅读顺序、关键文件、设计模式速查
