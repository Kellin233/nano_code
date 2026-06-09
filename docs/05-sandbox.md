# Sandbox：Shell 沙箱

## 概述

`capabilities/sandbox/` 只负责一件事：**限制 `run_shell` 命令的执行边界**。文件工具（read_file/write_file/edit_file）在宿主机执行，由权限系统保护——sandbox 不管它们。

## 架构

```
用户选 Profile（我要多安全？）
  → config.py 展开成 SandboxConfig
  → SandboxManager 根据 config 选 Backend（命令在哪跑？）
  → Backend 按 Policy 执行 run_shell
```

Profile/Backend/Policy 三层分离。用户只需理解 Profile，不需要记住 bwrap 参数。

## 三种 Backend

| Backend | 执行位置 | 隔离级别 | 适用场景 |
|---------|---------|:--:|------|
| `LocalBackend` | 宿主机 `subprocess.run` | 无 | 兼容、调试 |
| `BwrapBackend` | bubblewrap namespace | OS 级 | **Linux 默认**，日常开发 |
| `MicrosandboxBackend` | microVM + OCI image | 虚拟机级 | 不信任命令、强隔离 |

## BwrapBackend（默认）

使用 bubblewrap 创建 Linux namespace：

```
✅ /usr /bin /lib /etc   只读挂载（宿主机工具链可用）
✅ workspace             可写或只读挂载
✅ /tmp                  tmpfs
❌ /home /root           不挂载
❌ 网络                  默认关闭（--unshare-net）
❌ API key 等            不传入环境变量
```

**为什么是默认**：能用宿主机 Python/Node/Rust/Go 工具链，又能限制命令不要随便写 home 或访问网络。适合个人 Linux 日常开发。

## MicrosandboxBackend

真正的 microVM 隔离。guest 跑 OCI image（如 `python:3.12`），看不到宿主机文件系统。workspace 通过挂载暴露。

**为什么不是默认**：microVM 启动要数秒到数十秒，且 image 里可能缺宿主工具链（没有 Node、Rust 等）。适合明确需要强隔离的场景。

## 七个 Profile

| Profile | Backend | Workspace | 网络 | 用途 |
|---------|---------|-----------|:--:|------|
| `workspace` | bwrap | 可写 | 关 | 默认日常开发 |
| `read-only` | bwrap | 只读 | 关 | 代码审查 |
| `local` | local | 全访问 | 开 | 兼容调试 |
| `danger-full-access` | local | 全访问 | 开 | 明确全访问 |
| `microsandbox-dev` | microsandbox | 可写 | 关 | 隔离跑测试 |
| `microsandbox-safe` | microsandbox | 只读 | 关 | 不信任命令 |
| `microsandbox-strict` | microsandbox | 只读 | 关 | 禁止回退 |

## SandboxManager

```python
class SandboxManager:
    config: SandboxConfig          # frozen 配置
    _backend: SandboxBackend       # 懒初始化
    _lock: asyncio.Lock            # 并发保护

    async def run_shell(command, timeout_ms, cwd) -> str:
        backend = await _ensure_started()   # 首次调用时创建 Backend
        result = await backend.run_shell(command, timeout_ms, cwd)
        return result.to_tool_output()
```

会话级——每个 Agent 一个 SandboxManager 实例。子 Agent 复用父 Agent 的。

## 与权限系统的关系

```
permission：执行前，判断能不能试
sandbox：执行中，限制命令能碰什么
```

两者是独立维度。`--yolo` 跳过权限确认，不代表关闭 sandbox。`--sandbox microsandbox-safe` 不影响权限确认。

## 面试考点

**Q: sandbox 为什么只管 run_shell，不管文件工具？**

文件工具（read/write/edit）在宿主机 Python 进程执行。把它们迁进 sandbox 需要让整个 Python 进程跑在容器里——这会显著增加复杂度和启动时间。当前设计是务实的：文件工具靠权限系统保护（先读后改 + workspace 边界 + deny 规则），shell 命令靠 OS 级隔离。
