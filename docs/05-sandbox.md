# Shell 沙箱

## 1. 为什么需要 Sandbox

权限系统问用户"允不允许跑这条命令"。用户点了 yes。这条命令现在能访问整个文件系统、能联网、能读环境变量。如果命令是模型生成的——你真的信任它吗？

Sandbox 是执行层防线。它限制 shell 命令实际能碰哪些文件、能不能联网、能不能读敏感环境变量。**关键约束**：sandbox 只管 `run_shell`。文件工具（read_file/write_file/edit_file）在宿主机执行，靠权限系统保护。

## 2. 核心概念

### 2.1 Profile / Backend / Policy 三层

```
用户选 Profile（"我要多安全？"）
  → config.py 展开成 SandboxConfig
  → SandboxManager 选 Backend（"命令在哪跑？"）
  → Backend 按 Policy 执行（"能碰什么？"）
```

用户只需理解 Profile，不需要记住 bwrap 参数。代码内部保留 Backend 抽象，以后可加 Docker/Remote 后端。

### 2.2 三种 Backend

| Backend | 怎么跑 | 隔离 | 适用 |
|---------|--------|:--:|------|
| BwrapBackend | bubblewrap namespace | OS 级 | **Linux 默认** |
| LocalBackend | subprocess.run | 无 | 兼容/调试 |
| MicrosandboxBackend | microVM + OCI image | 虚拟机级 | 强隔离 |

### 2.3 BwrapBackend 的隔离边界

✅ `/usr` `/bin` `/lib` 只读（宿主工具链可用）→ workspace 只读或可写 → `/tmp` tmpfs。❌ `/home` `/root` 不挂载 → 网络默认关 → API key 不传入。

**为什么是默认**：能跑宿主机的 Python/Node/Rust，又能限制不乱写 home 或联网。日常开发的最佳平衡。

## 3. 总体设计

```
capabilities/sandbox/
├── types.py              # SandboxConfig、CommandResult
├── config.py             # build_sandbox_config()：CLI args → SandboxConfig
├── manager.py            # SandboxManager：按 config 选 backend，懒启动
├── backend.py            # SandboxBackend Protocol + LocalBackend
├── bwrap_backend.py      # Bubblewrap OS 级沙箱
└── microsandbox_backend.py  # microVM 强隔离
```

### 七个 Profile

| Profile | Backend | Workspace | 网络 | 用途 |
|---------|---------|-----------|:--:|------|
| workspace | bwrap | 可写 | 关 | 默认日常 |
| read-only | bwrap | 只读 | 关 | 审查 |
| local | local | 全访问 | 开 | 兼容 |
| danger-full-access | local | 全访问 | 开 | 明确全访问 |
| microsandbox-dev | microsandbox | 可写 | 关 | 隔离测试 |
| microsandbox-safe | microsandbox | 只读 | 关 | 不信任命令 |
| microsandbox-strict | microsandbox | 只读 | 关 | 禁止回退 |

## 4. 详细设计

**`manager.py`**：`SandboxManager` 会话级实例。`_build_backend()` 按 config.backend 选实现。`_ensure_started()` 懒初始化——首次 `run_shell` 时才创建 Backend。`describe()` 返回人可读的 sandbox 状态。子 Agent 复用父 Agent 的 SandboxManager。

**`bwrap_backend.py`**：bwrap 命令拼接——`--unshare-pid --unshare-ipc --unshare-net`。workspace bind mount 可写/只读。protected paths ro-bind。网络关闭策略：`--unshare-net`。

## 5. 设计决策

### 为什么默认 bwrap 而非 microsandbox

MicroVM 启动慢（数秒到数十秒），缺宿主工具链。bwrap 是"够用的隔离 + 完整工具链"的平衡。microsandbox 作为进阶——用户显式选择。

### 为什么 sandbox 只管 run_shell

把文件工具迁进 sandbox 需要整个进程跑容器——复杂度暴增。文件靠权限系统，shell 靠 OS 隔离。两条防线各管各的。

### 为什么 fail closed

bwrap 不可用时返回错误，不静默回退 local。只有显式 `--sandbox-allow-local-fallback` 才允许。microsandbox-strict 在任何情况不 fallback。

## 6. 面试考点

**Q: sandbox 为什么只管 run_shell？** 文件工具在宿主机执行。迁进 sandbox 需要进程级容器化——复杂度暴增。文件靠权限，shell 靠隔离。

**Q: 为什么默认不是 microsandbox？** 启动慢，缺宿主工具链。bwrap 是日常开发的最佳平衡。

## 7. 代码导读

**关键代码**：`manager.py:34-45` _build_backend 后端选择、`manager.py:69-85` _ensure_started 懒启动、`manager.py:87-105` fallback 策略、`bwrap_backend.py` bwrap 命令拼接。
