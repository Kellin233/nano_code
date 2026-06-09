* [引言](00-introduction.md)
  * 项目定位与技术选型
  * 核心设计哲学（Agent 纯状态、Backend 策略类、独立变更原因、能力模板一致性）
  * 架构全景图与模块依赖关系
  * 一次用户请求的完整数据流路径

* [总体设计与架构](01-architecture.md)
  * 模块全景图：cli/runtime/backend/capabilities/context 依赖关系
  * 关键设计决策：Agent 纯状态、Backend 策略类、双消息历史、事件工厂函数
  * 代码划分原则：独立变更原因、能力模板一致性、依赖方向单向

* [代码导读](15-code-reading-guide.md)
  * 推荐阅读顺序：从 cli/main.py 跟完一次请求
  * 关键文件标注：作用 + 复杂度评级 + 组织理由
  * 常见修改路径："加新工具"读哪些改哪些
  * 设计模式速查：策略模式、模板方法、工厂函数、事件流

* [1. 智能体循环](01-agent-loop.md)
  * Agent 状态容器：为什么从 Mixin 改为纯数据类？
  * AgentLoop 主循环：状态机图 + 每步代码路径
  * Backend 策略模式：接口设计意图
  * 双后端消息格式差异：为什么不强行统一？
  * 事件驱动模型：RuntimeEvent 工厂函数 vs 子类
  * ⚡ 面试考点：加第三个模型厂商改哪些文件？

* [2. 工具系统](02-tools.md)
  * 工具三层模型：Schema 定义 → Registry 注册 → Runtime 执行
  * builtin.py 的组织：为什么 schema 和实现在同一个文件？
  * ToolRegistry 的 deferred 机制：延迟激活的设计理由
  * ToolRuntime 执行管线：验证 → 权限 → Hook → 执行 → 后处理
  * 并发安全工具：哪些可并行？batch 分组调度
  * 先读后改不变量：mtime 检查的实现
  * ⚡ 面试考点：工具调用失败自动重试改哪里？

* [3. 系统提示词工程](03-system-prompt.md)
  * 稳定提示词 vs 动态附件：缓存友好的分离设计
  * 提示词结构剖析：每个 section 的设计意图
  * 动态附件的类型与注入时机
  * CLAUDE.md 加载机制：优先级、include 解析、HTML 剥离
  * ⚡ 面试考点：改什么内容不会让 Anthropic prompt cache 失效？

* [4. CLI 与会话](04-cli-session.md)
  * CLI 三层入口：args.py → main.py → runtime/
  * 三种运行模式：一次性 / 交互式 TUI / JSONL Server
  * 配置解析优先级：CLI 参数 > 环境变量 > 默认值
  * 会话持久化：append-only JSONL + ArtifactStore
  * ⚡ 面试考点：新增实时 Token 计数显示改哪些文件？

* [5. 流式输出与双后端](05-streaming.md)
  * BackendResponse 统一返回格式
  * Anthropic 流式解析：content_block_start/delta/stop
  * OpenAI 流式解析：增量 tool_calls 拼接
  * Token 统计与成本估算
  * 指数退避重试：可重试错误判断 + 退避公式
  * ⚡ 面试考点：Anthropic thinking block 为何要从历史中过滤？

* [6. 权限与安全](06-permissions.md)
  * 四种权限模式：default / acceptEdits / bypassPermissions / dontAsk
  * 权限检查顺序：protected path → workspace → deny → confirm
  * Shell 安全：禁裸 subprocess.run(shell=True)
  * Sandbox 后端：local / bwrap / microsandbox 场景差异
  * ⚡ 面试考点：--yolo 跳过了哪些检查？真的全跳过了吗？

* [7. 上下文管理](07-context.md)
  * 上下文窗口压力模型：三层压缩流水线（Budget → Snip → Microcompact）
  * Budget 层：按利用率动态裁剪
  * Snip 层：文件去重 + 陈旧判断
  * Compact 对话摘要：模型生成摘要 + 重挂 active skill
  * ⚡ 面试考点：长对话摘要质量不够好，改进方向有哪些？

* [8. 记忆系统](08-memory.md)
  * 存储格式：frontmatter + markdown body，文件系统即数据库
  * 召回流水线：本地匹配 → 侧查询选择 → 预算打包注入
  * 时间衰减与新鲜度警告
  * 记忆生命周期：创建 → 访问 → 整理 → 索引
  * ⚡ 面试考点：为什么不接向量数据库？文件系统优劣？

* [9. 技能系统](09-skills.md)
  * Skill 发现机制：用户级 vs 项目级的优先级覆盖
  * 调用模式：inline（注入提示词）vs fork（子 Agent 执行）
  * 参数渲染：$ARGUMENTS / $0 / ${CLAUDE_SKILL_DIR}
  * Active Skill 管理：compact 后重挂、token 预算
  * ⚡ 面试考点：fork 模式为什么需要独立 Agent 实例？

* [10. 计划模式](10-plan-mode.md)
  * 实现方式：agent 工具 + plan 子 Agent 类型
  * 与 Claude Code Plan Mode 的区别
  * ⚡ 面试考点：为什么让模型自己决定何时计划，而非强制？

* [11. 多 Agent 架构](11-multi-agent.md)
  * 子 Agent 创建：Fork-and-Return 模式
  * 三种内置类型：Explore / Plan / General 的工具限制
  * SubAgentOrchestrator 并行编排
  * 安全模型：工具白名单为主，Sandbox 透明继承
  * ⚡ 面试考点：两个子 Agent 需要协作怎么做？

* [12. MCP 集成](12-mcp.md)
  * MCP 协议栈：transport → connection → manager
  * 工具注册：mcp__server__tool 命名 + ToolRegistry.add_many
  * 工具变更通知：增量更新机制
  * ⚡ 面试考点：MCP 服务进程崩溃，当前有重连机制吗？

* [13. 架构对比与下一步](13-whats-next.md)
  * 与 Claude Code / Codex CLI / Aider 的架构对比
  * 已知局限与优先改进项
  * 后续 Roadmap
  * ⚡ 面试考点：如果你是 tech lead，接下来 3 个月 roadmap？

* [14. 功能测试指南](14-testing.md)
  * 测试目录结构与模块对应关系
  * 测试分层：单元 vs 集成的边界
  * Mock 策略：如何 mock Backend / Sandbox
  * 新增测试 checklist
