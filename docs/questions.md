下面按“严格面试官”视角拆成 5 组：项目描述 + 4 个核心职责。问题会偏追问式，重点考察你是否真的理解架构边界、指标来源、失败模式和取舍。

**一、项目描述：本地 Code Agent CLI**

1. 你说这个项目面向“代码仓库长链路修改、调试和命令执行场景”，请举一个完整任务链路，从用户输入到最终修改、验证、总结，每一步系统做了什么？

2. 为什么选择 CLI 作为交互入口，而不是 Web IDE、VSCode 插件或服务端 Agent？CLI 在权限、安全、上下文和复盘上有什么天然优势或劣势？

3. “长链路任务”相比普通一次性 LLM 问答，最大的工程难点是什么？请按上下文、工具、状态、用户确认四个维度分别说明。

4. 本地 Code Agent 和云端 Code Agent 的关键差异是什么？本地执行时你如何处理文件系统权限、环境变量、shell 命令和用户隐私？

5. 如果模型在长任务中连续执行 20 次工具调用，你如何保证系统仍然可控、可恢复、可审计？哪些信息必须持久化，哪些不应该持久化？

6. 你如何定义这个项目“成功”？是任务完成率、工具调用正确率、权限拦截率、上下文压缩率，还是用户体验？这些指标之间有没有冲突？

**二、Agent 系统架构设计**

1. 你说设计了 Agent Core、Harness、Runtime 三层架构。请分别说明三层的职责边界，以及每层不应该做什么。

2. Agent Core 为什么不能直接依赖工具系统、MCP、沙盒或模型后端？如果直接依赖，会带来什么工程问题？

3. Agent Loop 通过回调接入外部能力。请具体说明有哪些回调，例如工具执行、上下文准备、生命周期事件、Stop Hook 等，它们分别在 loop 的哪个阶段触发。

4. 你的 Agent Loop 如何处理模型返回的几种情况：纯文本、tool call、tool error、provider exception、用户 abort、预算超限？

5. Provider 后端为什么需要抽象？Anthropic 和 OpenAI-compatible API 在消息结构、tool call streaming、usage 统计上有哪些差异？你如何统一成内部协议？

6. Harness 里放了上下文管理、权限控制、对话持久化和任务恢复。为什么这些不放在 Runtime？为什么它们也不属于 Agent Core？

7. 如果现在要接入第三个模型厂商，比如 Gemini，你需要改哪些文件？如果你发现必须改 Agent Loop，说明当前抽象哪里有问题？

8. 你如何验证“分层没有被破坏”？有没有架构测试、import 约束或静态检查？如果有人在 Agent Core 里 import 了 CLI 模块，会造成什么后果？

**三、Tool Calling 与权限管理**

1. 你说多来源工具接入统一入口。内置工具、MCP 工具、Skill、SubAgent 在注册、暴露给模型、执行时有什么共同点和差异？

2. 一个工具调用从模型生成到真正执行，完整 pipeline 是什么？请按顺序说明 schema 校验、Hook、权限、确认、执行、结果处理、审计记录。

3. 为什么权限判断要发生在工具执行前？如果权限拒绝后直接中断整个 Agent Loop，会有什么问题？为什么要把拒绝作为 ToolResult 返回给模型？

4. 你如何区分 read-only 工具、edit 工具、shell 工具和编排类工具？这些分类如何影响权限、并发和上下文管理？

5. 危险 shell 命令如何识别？正则规则能覆盖哪些风险，不能覆盖哪些风险？为什么还需要 sandbox？

6. `--yolo`、`acceptEdits`、`dontAsk` 这类权限模式的差异是什么？为什么 `yolo` 也不能绕过 protected path 或 deny rule？

7. 你提到“越权工具测试中实现 100% 拦截率”。这个 100% 是怎么定义的？是模型没有请求越权工具，还是请求了但 runtime 成功拦截？两者指标有什么区别？

8. 工具并发策略如何设计？哪些工具可以并发，哪些必须串行？如果两个 `edit_file` 并发修改同一个文件，会出现什么风险？

9. MCP 工具为什么默认 deferred？`tool_search` 的作用是什么？如果 MCP server 暴露 100 个工具，直接塞进 prompt 会有什么问题？

**四、上下文与会话状态管理**

1. 你说上下文三级压缩流水线包括大结果落盘、历史裁剪、旧对话摘要。请分别说明每一级的触发时机、输入、输出和不可替代性。

2. 为什么大工具结果要在 ToolRuntime 阶段落盘，而不是等下一次 provider call 前再处理？

3. Tool History Snip 为什么只替换旧 tool result 内容，而不是直接删除历史消息？这和 provider 的 tool_use / tool_result 协议有什么关系？

4. Context Compact 为什么要保留最近原文，而不是把所有历史都摘要成一段？对于代码修改任务，最近原文有什么特殊价值？

5. 压缩后状态恢复具体恢复什么？项目指令、memory、active skills、deferred tools、recent files 分别解决什么问题？

6. 你提到平均上下文 Token 压缩率 50.2%。这个指标怎么计算？分母是压缩前 provider payload token，还是整段 conversation token？不同任务长度会不会影响平均值？

7. append-only 会话日志保存哪些事件？为什么不直接保存完整 snapshot JSON？append-only 在恢复、审计和损坏修复上有什么优势？

8. 未完成工具调用如何修复？什么是 orphan tool call？如果 assistant message 里有 tool_use 但没有对应 tool_result，下一次 provider call 会发生什么？

9. Resume 时应该恢复哪些状态，哪些状态不应该恢复？例如 sandbox 进程、MCP 连接、pending approval、recent file cache、token usage，分别怎么处理？

10. 如果 compact 过程中摘要模型调用失败，系统应该继续、重试还是中断？连续失败时如何避免每轮都重复失败？

**五、评测与审计闭环**

1. 你说构建了覆盖 12 类、41 个任务的 Agent Benchmark。12 类分别是什么？每类任务主要验证哪个系统能力？

2. Benchmark 的任务完成率 95.1% 是怎么计算的？是否所有任务权重相同？失败任务是否区分模型能力失败、工具失败、权限误杀、上下文丢失？

3. 你的 benchmark verifier 是如何设计的？为什么要用 fixture workspace + shell verifier，而不是只看模型最终回答？

4. “权限拦截率 100%”和“整体任务完成率 95.1%”之间可能存在冲突吗？如果权限过严导致任务失败，你会如何权衡？

5. Run artifacts 里 trace 和 report 分别记录什么？为什么 trace 不能作为 resume 的事实来源？

6. 你如何复盘一次失败任务？请说出你会先看哪些文件、哪些字段、如何判断失败发生在 provider、工具执行、权限、上下文还是 verifier。

7. 消融实验是怎么做的？关闭上下文压缩、关闭 resume、关闭 memory 时，如何保证对照组公平？是否固定模型、任务、prompt、工具集合和随机性？

8. 你说评估了 Checkpoint / Resume 模块效果和能力边界。Resume 成功的判据是什么？仅仅恢复 session 不等于任务续跑成功，你如何区分？

9. Benchmark 中如何防止模型“作弊”？例如直接猜 verifier 目标、不读文件就写答案、绕过指定工具、修改不该修改的文件。

10. 如果后续要把 benchmark 从 41 个任务扩展到 200 个任务，你会如何组织任务 schema、fixture、verifier、指标聚合和失败归因？