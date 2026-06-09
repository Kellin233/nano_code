# Shell 沙箱

## 为什么需要 Sandbox

模型调用 `run_shell("npm test")`——这条命令能访问整个文件系统、能联网、能读环境变量。如果不在执行层加限制，权限系统的"确认"是唯一防线——而用户可能点了 `--yolo`。

**Sandbox 是执行层的防线**。它限制 shell 命令实际能碰哪些文件、能不能联网、能不能读敏感环境变量。和权限系统互补：权限管"能不能试"，sandbox 管"试的时候边界在哪"。

**关键约束**：sandbox 只管 `run_shell`。`read_file`/`write_file`/`edit_file` 在宿主机 Python 进程中执行，靠权限系统保护。

## 核心概念

### Profile / Backend / Policy 三层

```
用户选 Profile（我要多安全？）
  → config.py 展开成 SandboxConfig
  → SandboxManager 选 Backend（命令在哪跑？）
  → Backend 按 Policy 执行 run_shell（能碰什么？）
```

用户只需理解 Profile（"workspace"、"read-only"、"microsandbox-safe"），不需要记住 bwrap 参数。代码内部保留 Backend 抽象——以后可以加 Docker 或 Remote 后端。

### 三种 Backend

| Backend | 怎么跑的 | 隔离级别 | 适用 |
|---------|---------|:--:|------|
| BwrapBackend | bubblewrap namespace | OS 级 | **Linux 默认** |
| LocalBackend | subprocess.run | 无 | 兼容/调试 |
| MicrosandboxBackend | microVM + OCI image | 虚拟机级 | 强隔离 |

### BwrapBackend 的隔离边界

```
✅ /usr /bin /lib      只读——宿主机工具链可用
✅ workspace           可写或只读（按 profile）
❌ /home /root         不挂载
❌ 网络                默认关闭
❌ API key 等          不传入环境变量
```

**为什么是默认**：能用宿主机的 Python/Node/Rust/Go，又能限制命令不要乱写 home 或访问网络。

## 设计决策

### 为什么默认是 bwrap 而非 microsandbox

Microsandbox 隔离更强——microVM 级别。但启动要数秒到数十秒，OCI image 里没有宿主的 Node/Rust 等工具链。日常开发跑 `npm test` 直接失败。bwrap 实现了"够用的隔离 + 完整的工具链"。microsandbox 作为进阶——用户显式选择。

### 为什么 sandbox 只管 run_shell 不管文件工具

把文件工具迁进 sandbox 需要整个 Agent 跑在容器里——复杂度暴增。当前是务实的两条防线：文件工具靠权限系统（先读后改 + workspace 边界 + deny 规则），shell 命令靠 OS 隔离。

### 为什么 fail closed 是默认

bwrap 不可用时返回错误提示安装 bubblewrap，不静默回退到 local。只有用户显式 `--sandbox-allow-local-fallback` 才允许。microsandbox-strict 在任何情况下都不 fallback。

## 代码走读

**`config.py`**：`build_sandbox_config(args)` 把 CLI 参数 + 环境变量合并成 `SandboxConfig`。Profile 是用户心智，Backend 是实现细节。

**`manager.py`**：`SandboxManager` 会话级实例。`_build_backend()` 按 config.backend 选实现，`_ensure_started()` 懒初始化。子 Agent 复用父 Agent 的 SandboxManager。

**`backend.py`**：`SandboxBackend` Protocol + `LocalBackend`。`LocalBackend` 就是 `subprocess.run(shell=True)`——明确标为无隔离。

**`bwrap_backend.py`**：bubblewrap 命令拼接——`--unshare-pid --unshare-net`、`--ro-bind /usr`、`--bind workspace`。

## 面试考点

**Q: sandbox 为什么只管 run_shell？**

文件工具在宿主机 Python 进程执行。迁进 sandbox 需要整个 Agent 跑容器里——复杂度暴增。当前是务实的两条防线：文件靠权限，shell 靠 OS 隔离。

**Q: 为什么默认不是 microsandbox？**

MicroVM 启动慢（数秒到数十秒），OCI image 缺少宿主工具链（没 Node、没 Rust）。日常开发体验差。bwrap 是"够用的隔离+完整工具链"的平衡点。
