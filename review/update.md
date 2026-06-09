# src 优化方案 —— 2026-06-08

## 一、优化范围

本次优化聚焦于以下可安全实施的改进，不做大规模架构重构：

1. compact 过程缺少异常保护 → 添加降级处理
2. BUILTIN_HANDLERS 中的 run_shell 死代码 → 移除并标注
3. 散落魔数收敛到统一常量
4. 模型名默认值集中管理
5. `_find_tool_use_by_id` O(n²) 查询优化
6. `except Exception: pass` 改为 diagnostics 收集

---

## 二、逐项方案

### 2.1 compact 异常保护 [高]

**问题**：`_compact_conversation()` 中如果 API 调用失败（限流、网络中断），异常会直接传播到对话循环并中断用户会话。

**方案**：在 `_compact_conversation` 外层捕获异常，记录错误并降级——用未 compact 的历史继续对话。同时在 UI 显示警告。

**文件**：[src/runtime/agent/context.py](src/runtime/agent/context.py#L207-L219)

### 2.2 移除 BUILTIN_HANDLERS 死代码 [中]

**问题**：`BUILTIN_HANDLERS["run_shell"]` 在两个调用路径上都已被短路（之前修复的 run_shell 安全加固），不再有代码路径能到达这个 handler。

**方案**：从 `BUILTIN_HANDLERS` 中移除 `run_shell`，添加注释说明。`builtin.py` 中的 `run_shell` 函数保留（作为纯函数实现参考，但不再被 handler 字典引用）。

**文件**：[src/domains/tools/runtime.py](src/domains/tools/runtime.py#L181-L193)

### 2.3 收敛魔数到常量 [低]

**问题**：压缩阈值、超时、上下文窗口安全边距等魔数散落在 6+ 个文件中。

**方案**：在 `src/domains/tools/constants.py` 中集中管理，其他模块导入使用。

涉及常量：
- `MAX_RESULT_CHARS = 50000`
- `LARGE_RESULT_BYTES = 30 * 1024`
- `SNIP_THRESHOLD = 0.60`
- `MICROCOMPACT_IDLE_S = 5 * 60`
- `KEEP_RECENT_RESULTS = 3`
- `CONTEXT_WINDOW_MARGIN = 20000`
- `DEFAULT_MAX_TOKENS = 16384`
- `DEFAULT_TIMEOUT_MS = 30000`
- `DEFAULT_MAX_LENGTH = 50000`

### 2.4 模型名默认值集中 [低]

**问题**：`"claude-opus-4-6"` 散落在 `core.py`、`app_server.py` 等多处。

**方案**：在 `models.py` 中定义 `DEFAULT_MODEL = "claude-opus-4-6"`，其他模块导入。

**文件**：[src/runtime/agent/models.py](src/runtime/agent/models.py)、[src/runtime/agent/core.py](src/runtime/agent/core.py#L91)、[src/server/app_server.py](src/server/app_server.py#L129)

### 2.5 `_find_tool_use_by_id` 查询优化 [中]

**问题**：`_snip_stale_results_anthropic` 中每个 tool_result 都调用 `_find_tool_use_by_id`，该方法 O(n) 扫描全部消息。当消息历史很长时有 O(m×n) 复杂度。

**方案**：在 `_snip_stale_results_anthropic` 中先构建一次 `id → (name, input)` 的索引 map，后续 O(1) 查找。

**文件**：[src/runtime/agent/context.py](src/runtime/agent/context.py#L314-L351)

### 2.6 `except Exception: pass` 替换 [低]

**问题**：`context.py` 中三处 `except Exception: pass` 在出错时完全静默。

**方案**：改为收集到 `self._diagnostics: list[str]` 列表中，可供调试排查。

**文件**：[src/runtime/agent/context.py](src/runtime/agent/context.py#L117-L119)

---

## 三、不变范围

以下项目本次不做：
- Anthropic ↔ OpenAI 双路径统一（需要架构重构，风险大）
- 新旧 provider 体系合并（正在进行中的重构，不宜介入）
- `capabilities/` 插件协议标准化（需要设计讨论）
- sandbox backend 注册表模式（需要设计讨论）
- 测试覆盖补齐（留待后续专项）

---

## 四、验证计划

1. `python -m compileall src test`
2. `python -m unittest discover -s test -v`
3. `python -m unittest discover -s test/v1 -v`
4. 全部 177 个测试保持通过，零回归
