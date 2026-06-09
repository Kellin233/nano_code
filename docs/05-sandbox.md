# Shell 沙箱

## 1. 为什么需要 Sandbox

权限系统问用户"允不允许跑这条命令"。用户点 yes。现在这条命令能访问整个文件系统、能联网、能读环境变量。如果命令是模型生成的——你完全信任它吗？

Sandbox 是执行层防线。它限制 shell 命令实际能碰哪些文件、能不能联网、能不能读敏感环境变量。和权限互补：权限管"能不能试"，sandbox 管"试的时候边界在哪"。

**一条关键约束贯穿整个设计**：sandbox 只管 `run_shell`。`read_file`、`write_file`、`edit_file` 在宿主机 Python 进程中执行，由权限系统保护。不把文件工具迁进 sandbox 是刻意的——迁进去需要整个 Agent 进程跑容器，复杂度暴增但安全收益有限。务实的两条防线：文件靠权限（先读后改 + workspace + deny），shell 靠 OS 隔离。

## 2. 核心概念

### 2.1 Profile / Backend / Policy 三层分离

```
用户选择 Profile（"我要多安全？"）
    → workspace / read-only / microsandbox-safe / local
    │
    ├── config.py 展开成 SandboxConfig
    │     profile="workspace" → backend="bwrap", workspace_mode="write", network="none"
    │
    ├── SandboxManager 根据 config.backend 选 Backend
    │     "bwrap" → BwrapBackend
    │     "local" → LocalBackend
    │     "microsandbox" → MicrosandboxBackend
    │
    └── Backend 按 Policy 执行 run_shell
          能写哪里？能不能联网？能不能读环境变量？
```

三层分离意味着：用户只需理解 Profile（7 个选项），不需要知道 bwrap 参数。代码内部保留 Backend 抽象——以后加 Docker/Remote 后端不破坏用户接口。Policy 是 SandboxConfig 的字段——不独立成模块。

### 2.2 三种 Backend 的隔离边界

**LocalBackend**：`subprocess.run(command, shell=True, cwd=cwd)`。无任何隔离——能访问整个文件系统、所有环境变量、完整网络。标记 `backend_name="local"`，`is_sandboxed=false`。价值：兼容和调试。不是安全选项。

**BwrapBackend**（Linux 默认）：bubblewrap namespace 隔离。`--unshare-pid --unshare-ipc --unshare-uts --unshare-net`。/usr /bin /lib /etc 只读挂载（宿主机工具链可用）。workspace 按 profile 可写或只读挂载。/tmp tmpfs。/home /root 不挂载。API key 等敏感环境变量不传入。

**MicrosandboxBackend**（进阶）：microVM + OCI image（如 python:3.12）。guest 看不到宿主机文件系统。workspace 通过挂载暴露。CPU、内存、网络均可控。

### 2.3 七个 Profile

| Profile | Backend | Workspace | 网络 | fallback | 用途 |
|---------|---------|-----------|:--:|:--:|------|
| workspace | bwrap | 可写 | 关 | 不可 | **Linux 默认**，日常开发 |
| read-only | bwrap | 只读 | 关 | 不可 | 审查、分析 |
| local | local | 全访问 | 开 | N/A | 兼容/调试 |
| danger-full-access | local | 全访问 | 开 | N/A | 明确全访问 |
| microsandbox-dev | microsandbox | 可写 | 关 | 不可 | 隔离跑测试 |
| microsandbox-safe | microsandbox | 只读 | 关 | 不可 | 不信任命令 |
| microsandbox-strict | microsandbox | 只读 | 关 | 禁止 | 最保守 |

## 3. 总体设计

```
capabilities/sandbox/
├── types.py                  # SandboxConfig、CommandResult、SandboxProfile
├── config.py                 # build_sandbox_config(args) → SandboxConfig
├── manager.py                # SandboxManager：backend 选择 + 生命周期
├── backend.py                # SandboxBackend Protocol + LocalBackend
├── bwrap_backend.py          # Bubblewrap backend（Linux 默认）
└── microsandbox_backend.py   # Microsandbox microVM backend
```

## 4. 详细设计

### 4.1 types.py——配置与结果

`SandboxConfig`（frozen dataclass）：`profile`（用户心智）、`backend`（实现选择）、`workspace_host_path`/`workspace_guest_path`（路径映射）、`workspace_mode`（read-only|workspace-write|full-access）、`network_mode`（none|default）、`fail_if_unavailable`、`allow_fallback_to_local`。microsandbox 专用：`image`/`cpus`/`memory_mib`/`startup_timeout_s`。local/bwrap 专用：`protected_paths`（.git/.env/.claude 等）、`extra_writable_roots`、`forwarded_env`。

`CommandResult`：`stdout`、`stderr`、`exit_code`、`timed_out`、`backend_name`、`error`。`to_tool_output(timeout_ms)` 转为工具结果字符串。

### 4.2 manager.py——生命周期

`SandboxManager` 是会话级实例——每个 Agent 一个。核心方法：

`_build_backend()`：按 `config.backend` 选实现。`"local"` → `LocalBackend()`，`"bwrap"` → `BwrapBackend(config)`，`"microsandbox"` → `MicrosandboxBackend(config, session_id)`。

`_ensure_started()`：懒初始化。首次 `run_shell` 时调用。检查 `is_available()`——不可用时判断 fallback 策略。`allow_fallback_to_local=True` 且不是 strict profile → 创建 `LocalBackend()`。否则抛出 RuntimeError。

`describe()`：返回人可读的 sandbox 状态字符串——profile、backend、isolation 类型、workspace 路径、读写模式、网络、protected paths、环境变量转发、fallback 策略。

### 4.3 bwrap_backend.py——Bubblewrap 实现

核心是构造 bwrap 命令参数列表。`--die-with-parent --new-session`（进程组隔离）。`--unshare-pid --unshare-ipc --unshare-uts`（namespace 隔离）。网络模式：`--unshare-net`（none）或者不加（default）。

文件系统挂载：`--proc /proc --dev /dev --tmpfs /tmp`。`--ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /lib /lib --ro-bind /etc /etc`（系统目录只读）。workspace 按 mode：`--bind workspace workspace`（可写）或 `--ro-bind workspace workspace`（只读）。

protected paths 策略：对 `.git`、`.env`、`.claude` 等路径做 `--ro-bind`（只读绑定）——如果单独绑定失败，记录 warning。

环境变量：只传 `PATH HOME=/tmp LANG LC_ALL TERM`。不传 `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`AWS_*`、`GITHUB_TOKEN` 等。用户可通过 `--sandbox-env NAME` 白名单传入。

## 5. 设计决策

### 决策 1：为什么默认 bwrap 而非 microsandbox

MicroVM 启动慢（数秒到数十秒），OCI image 里缺少宿主工具链（没有 Node/Rust/Go）。日常跑 `npm test` 直接失败。bwrap 提供了"够用的隔离+完整工具链"——能用宿主机所有工具，但限制文件系统和网络。microsandbox 作为进阶——用户显式选择。

### 决策 2：为什么 sandbox 只管 run_shell

文件工具迁进 sandbox 需要整个 Agent 进程跑容器——复杂度暴增。当前是务实的两条防线。文件靠权限（先读后改+workspace 边界+deny 规则），shell 靠 OS 隔离。

### 决策 3：为什么 fail closed

bwrap 不可用时返回错误提示安装，不静默回退 local。只有显式 `--sandbox-allow-local-fallback` 才允许。microsandbox-strict 任何情况不 fallback。静默回退会破坏信任——用户以为在 sandbox 里，实际在裸跑。

## 6. 面试考点

**Q: 为什么默认不是 microsandbox？** 启动慢、缺宿主工具链。bwrap 是日常开发的最佳平衡。

**Q: sandbox 为什么只管 run_shell？** 文件工具迁入需进程级容器化——复杂度暴增。文件靠权限，shell 靠隔离。

**Q: bwrap 不可用什么行为？** 返回错误提示安装 bubblewrap，不静默回退。只有显式 fallback 才允许。

**Q: protected paths 怎样实现？** bwrap 对 .git/.env/.claude 做 ro-bind。失败记录 warning 但不静默忽略。

## 7. 代码导读

**关键行号**：`manager.py:34-45` _build_backend()、`manager.py:69-85` _ensure_started 懒启动+fallback、`manager.py:87-105` fallback 策略、`manager.py:123-152` describe()、`bwrap_backend.py` bwrap 命令拼接。
