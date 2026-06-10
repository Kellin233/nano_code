# Shell 沙箱

## 1. 为什么需要 Sandbox

权限系统只能在执行前问“允不允许”。一旦用户允许执行 shell，命令能否访问主机文件、网络和环境变量，就需要 sandbox 控制。

Sandbox 是应用层能力，位于 `cli/core/sandbox/`。它只管 `run_shell`。`read_file`、`write_file`、`edit_file` 在宿主 Python 进程中执行，由权限系统和先读后改保护。

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
  workspace / read-only / local / microsandbox-safe ...
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

## 4. Profiles

| Profile | Backend | Workspace | 网络 | 用途 |
|---------|---------|-----------|:--:|------|
| `workspace` | bwrap | 可写 | 关 | Linux 默认，日常开发 |
| `read-only` | bwrap | 只读 | 关 | 审查、分析 |
| `local` | local | 全访问 | 开 | 兼容和调试 |
| `danger-full-access` | local | 全访问 | 开 | 明确全访问 |
| `microsandbox-dev` | microsandbox | 可写 | 关 | 隔离跑测试 |
| `microsandbox-safe` | microsandbox | 只读 | 关 | 不信任命令 |
| `microsandbox-strict` | microsandbox | 只读 | 关 | 最保守 |

## 5. Backend 边界

### LocalBackend

直接 `subprocess.run(..., shell=True)`。没有隔离，只用于兼容、调试或用户显式选择。

### BwrapBackend

Linux 默认。使用 bubblewrap namespace 隔离：

- workspace 按 profile 可写或只读挂载。
- `/usr`、`/bin`、`/lib`、`/etc` 只读挂载，保留宿主工具链。
- `/tmp` 为 tmpfs。
- 默认关闭网络。
- 不转发 API key 等敏感环境变量，除非用户显式 `--sandbox-env`。

### MicrosandboxBackend

使用 microVM 和 OCI image。隔离更强，启动成本更高，适合不信任命令或需要更硬边界的场景。

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

## 7. 设计决策

### 为什么默认 bwrap

microVM 启动慢，镜像里也未必有宿主项目需要的 Node/Rust/Go 等工具链。bwrap 对日常 Linux 开发是更务实的默认：隔离够用，同时可用宿主工具。

### 为什么不 sandbox 文件工具

把文件工具也放进 sandbox 需要整个 Agent 进程容器化，复杂度明显上升。当前边界是：文件操作靠权限和先读后改，shell 靠 OS 隔离。

### 为什么 fail closed

bwrap 不可用时默认报错，不静默回退 local。只有用户显式允许 fallback 时才回退，否则会破坏用户对 sandbox 的信任。

## 8. 代码导读

```
cli/core/sandbox/config.py
cli/core/sandbox/types.py
cli/core/sandbox/manager.py
cli/core/sandbox/bwrap_backend.py
cli/core/sandbox/microsandbox_backend.py
cli/core/tools/builtin.py      # run_shell 调用入口
```
