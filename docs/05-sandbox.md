# Shell 沙箱

## 1. 为什么需要 Sandbox

权限系统只能在执行前问“允不允许”。一旦用户允许执行 shell，命令能否访问主机文件、网络和环境变量，就需要 sandbox 控制。

Sandbox 是应用层能力，位于 `cli/core/sandbox/`。它只管 `run_shell`。`read_file`、`write_file`、`edit_file` 在宿主 Python 进程中执行，由权限系统、workspace/protected path 检查和原子写入约束保护。

## 2. 文件结构

```
cli/core/sandbox/
├── __init__.py
├── types.py                  # SandboxConfig、CommandResult、SandboxBackend、LocalBackend
├── config.py                 # CLI 参数 → SandboxConfig
├── manager.py                # SandboxManager：backend 选择、生命周期、fallback
├── bwrap_backend.py          # Bubblewrap backend
└── microsandbox_backend.py   # Microsandbox backend
```

`backend.py` 已合并进 `types.py`：协议 `SandboxBackend` 和 `LocalBackend` 放在同一个文件，避免只为两个小类型保留独立模块。

## 3. Profile / Backend / Policy

```
Profile（用户选择）
  workspace / read-only / local / microsandbox / microsandbox-safe ...
        │
        ▼
SandboxConfig（展开后的策略）
  backend、workspace_mode、network_mode、fallback、env allowlist
        │
        ▼
SandboxManager
  选择 LocalBackend / BwrapBackend / MicrosandboxBackend
        │
        ▼
Backend.run_shell(command)
```

用户主要理解 profile；实现层通过 backend 和 config 控制细节。

Profile 展开后会落到这些字段：

| 字段 | 含义 |
|------|------|
| `backend` | `local`、`bwrap` 或 `microsandbox` |
| `workspace_mode` | `workspace-write`、`read-only`、`full-access` |
| `network_mode` | `none` 或 `default` |
| `workspace_host_path` / `workspace_guest_path` | 宿主 workspace 和 sandbox 内路径 |
| `protected_paths` | bwrap workspace-write 下重新只读挂载的敏感路径 |
| `forwarded_env` | 显式允许转发到 sandbox 的环境变量 |
| `extra_writable_roots` | 额外允许写入的宿主目录 |
| `allow_fallback_to_local` | backend 不可用时是否允许显式回退 local |

## 4. Profiles

| Profile | Backend | Workspace | 网络 | 用途 |
|---------|---------|-----------|:--:|------|
| `workspace` | bwrap | 可写 | 关 | Linux 默认，日常开发 |
| `read-only` | bwrap | 只读 | 关 | 审查、分析 |
| `local` | local | 全访问 | 开 | 兼容和调试 |
| `danger-full-access` | local | 全访问 | 开 | 明确全访问 |
| `microsandbox` | microsandbox | 可写或只读 | 关 | 兼容别名，按 `--sandbox-readonly-workspace` 展开成 safe/dev |
| `microsandbox-dev` | microsandbox | 可写 | 关 | 隔离跑测试 |
| `microsandbox-safe` | microsandbox | 只读 | 关 | 不信任命令 |
| `microsandbox-strict` | microsandbox | 只读 | 关 | 最保守 |

默认 profile 由平台决定：Linux 使用 `workspace`，非 Linux 使用 `local`。`--sandbox-readonly-workspace` 会把非 local backend 的 workspace mode 收窄为只读。

## 5. Backend 边界

### LocalBackend

直接 `subprocess.run(..., shell=True)`。没有隔离，只用于兼容、调试或用户显式选择。

### BwrapBackend

Linux 默认。使用 bubblewrap namespace 隔离：

- workspace 按 profile 可写或只读挂载。
- `workspace-write` 模式下，已存在的 protected paths 会重新只读挂载，包括 `.git`、`.env`、`.env.*`、`.codex`、`.claude` 等 `SandboxConfig.protected_paths` 匹配项。
- `/usr`、`/bin`、`/lib`、`/etc` 只读挂载，保留宿主工具链。
- `/tmp` 为 tmpfs。
- 默认关闭网络。
- 不转发 API key 等敏感环境变量，除非用户显式 `--sandbox-env`。

bwrap 的重点不是“构建一个全新容器镜像”，而是在宿主工具链基础上加 namespace 边界。它只读挂载常见系统目录，让项目测试仍能找到 Python/Node/Git 等宿主工具；同时清空环境、重设 HOME、把 `/tmp` 变成 tmpfs，并按 profile 控制 workspace 可写性。

### MicrosandboxBackend

使用 microVM 和 OCI image。隔离更强，启动成本更高，适合不信任命令或需要更硬边界的场景。

Microsandbox 会把宿主 workspace 挂到 guest 的 `/workspace`。`SandboxManager.host_path_to_guest_path()` 只允许把 workspace 内路径映射到 guest；cwd 在 workspace 外会直接报错。SDK 不可用时，`microsandbox-strict` 一定失败；其他 microsandbox/bwrap profile 只有用户显式 `--sandbox-allow-local-fallback` 或环境变量 `NANO_CODE_SANDBOX_ALLOW_LOCAL_FALLBACK=1` 时才会回退 local。

Microsandbox 的隔离更强，但它依赖 SDK、image 和启动时间。适合不信任命令或需要 microVM 边界的场景；日常开发默认用 bwrap 是为了减少冷启动成本和工具链缺失问题。

## 6. 与权限系统的关系

```
ToolRuntime
  → check_permission(...)
  → confirm_fn(...)
  → run_shell builtin
  → SandboxManager.run_shell(...)
  → Backend.run_shell(...)
```

权限和 sandbox 不互相替代。`--yolo` 影响确认，不影响 sandbox profile。

## 7. Shell 执行流程

`run_shell` 的完整链路：

```text
模型产生 run_shell tool call
  → ToolRuntime allowlist / validate / hook / permission / confirm
  → builtin run_shell
  → SandboxManager.run_shell(command, timeout, cwd)
  → _ensure_started() 选择或启动 backend
  → backend cwd 映射
      local: 宿主绝对路径
      bwrap: workspace 内宿主路径
      microsandbox: /workspace 下 guest 路径
  → Backend.run_shell()
  → CommandResult.to_tool_output()
```

`SandboxManager` 会拒绝 workspace 外 cwd。backend 不可用时，只有显式允许 fallback 才会改用 local。

失败模式会被转换成普通工具输出：

- 命令超时：`Command timed out after <timeout>ms`。
- 非零退出：返回 exit code、stdout、stderr。
- backend/cwd/sandbox 启动错误：返回 `Error: ...`。
- bwrap/microsandbox 不可用且不允许 fallback：返回错误，不静默裸跑宿主 shell。

这些输出会作为 `run_shell` 的 `ToolResult` 回到模型，由模型决定是否修正命令、缩小测试范围或改用文件检查。

## 8. 设计决策

### 为什么默认 bwrap

microVM 启动慢，镜像里也未必有宿主项目需要的 Node/Rust/Go 等工具链。bwrap 对日常 Linux 开发是更务实的默认：隔离够用，同时可用宿主工具。

### 为什么不 sandbox 文件工具

把文件工具也放进 sandbox 需要整个 Agent 进程容器化，复杂度明显上升。当前边界是：文件操作靠权限、受保护路径检查和原子写入；shell 靠 OS 隔离。

### 为什么 fail closed

bwrap 不可用时默认报错，不静默回退 local。只有用户显式允许 fallback 时才回退，否则会破坏用户对 sandbox 的信任。

## 9. Benchmark 覆盖

`benchmarks/local-fixture` 不直接测试隔离实现细节，但多类任务依赖 sandbox 合同：

- `python_*`、`test_driven_fix`、`recovery_config_check` 使用 `run_shell` 做本地校验，要求 shell 统一从 `SandboxManager` 进入。
- security/permissions 任务验证权限先于 shell 执行；被 deny 的命令不能到达 sandbox。
- path escape 任务验证文件工具的 workspace boundary；这和 sandbox 是互补边界，不能互相替代。

## 10. 代码导读

```
cli/core/sandbox/config.py
cli/core/sandbox/types.py
cli/core/sandbox/manager.py
cli/core/sandbox/bwrap_backend.py
cli/core/sandbox/microsandbox_backend.py
cli/core/tools/builtin.py      # run_shell 调用入口
```
