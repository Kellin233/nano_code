# Sandbox 重构方案

## 目标

把 `nano_code` 的 sandbox 从“只支持 local / microsandbox 的 shell 后端切换”升级成更适合个人 Linux 日常使用的安全执行系统。

最终定位：

```text
默认：Codex CLI 类似的 Linux 本地 sandbox 思路
  宿主工具链 + OS 级隔离 + workspace 边界 + 网络默认关闭

进阶：microsandbox microVM 后端
  OCI image + microVM 级隔离 + 更强隔离 + 更可复现的干净环境
```

这不是要复刻 Codex CLI，也不是要把 microsandbox 的完整平台能力搬进来。`nano_code` 的核心仍然是 coding agent runtime。sandbox 只负责让模型生成的 shell 命令在清楚、可解释、可配置的边界内执行。

## 设计定位

sandbox 和 permission 是两层不同控制：

```text
permission：执行前，判断这个工具调用能不能尝试执行。
sandbox：执行中，限制 shell 命令实际能碰哪些文件、网络和系统资源。
```

因此：

- 所有工具调用仍必须先经过 `ToolRuntime`、hooks 和权限策略。
- sandbox 不负责判断“危险不危险”，只负责执行边界。
- 模型仍然只看到 `run_shell` 一个工具，不直接管理 sandbox。
- 文件工具第一阶段仍在宿主机执行，由权限系统保护。
- `run_shell` 默认进入 Linux 本地 sandbox；microsandbox 是高级隔离模式。

## 当前实现概述

当前代码已经有一个正确的基础：

```text
nano_code/sandbox/
├── types.py
├── config.py
├── backend.py
├── manager.py
└── microsandbox_backend.py
```

已有能力：

- `SandboxBackend` 抽象。
- `LocalBackend`。
- `MicrosandboxBackend`。
- `SandboxManager` 会话级懒启动、复用和清理。
- workspace host path 到 guest path 的映射。
- microsandbox image、CPU、内存、只读 workspace、断网配置。
- 显式 microsandbox 不可用时不静默回退。

需要改进：

- 默认 local 太弱，不适合作为安全默认值。
- 没有 Linux 本地 OS sandbox 后端。
- 没有清晰 profile，用户需要自己组合参数。
- 没有明确 secret policy。
- sandbox 内 protected path 边界不够清楚。
- 没有用户可见的 sandbox status / threat model。
- `microsandbox` 被放在默认候选位置，日常 Linux 开发体验可能不如宿主工具链 sandbox。

## 总体设计

### 一句话结论

新增 Linux 本地 `BwrapBackend`，让 `nano_code` 默认使用宿主工具链运行命令，同时用 `bubblewrap` 限制文件系统和网络边界。保留 `LocalBackend` 作为明确的无沙箱兼容模式，保留 `MicrosandboxBackend` 作为需要 microVM 强隔离时的进阶模式。

### 先看心智模型

sandbox 这块最容易讲乱，因为有三个相近但不同的词：profile、backend、policy。

在 `nano_code` 里，这三个词应该这样理解：

```text
Profile：用户选择的安全模式，回答“我要多安全/多兼容？”
Backend：命令实际在哪里跑，回答“run_shell 用哪个执行后端？”
Policy：执行边界配置，回答“能写哪里、能不能联网、能带哪些环境变量？”
```

运行时的展开关系是：

```text
用户选择 profile
  -> config.resolve_profile() 展开成 SandboxConfig
  -> SandboxManager 根据 config 选择 backend
  -> backend 按 policy 执行 run_shell
  -> CommandResult 返回给工具系统
```

这样做的目的有两个：

- 用户只需要理解 profile，不需要记住 bwrap/microsandbox 的所有参数。
- 代码内部仍然保留 backend 抽象，后续可以新增 `DockerBackend` 或 `RemoteBackend`，不破坏用户接口。

### 从用户角度看：三类模式

用户实际需要理解的是三类模式，而不是所有底层细节：

| 用户模式 | 推荐 profile | 含义 |
|----------|---------------|------|
| 日常开发默认 | `workspace` | 在 Linux 本地 sandbox 里运行命令，workspace 可写，网络默认关闭 |
| 只读检查 | `read-only` | 在 Linux 本地 sandbox 里运行命令，workspace 只读，适合审查和解释 |
| 强隔离执行 | `microsandbox-safe` / `microsandbox-dev` | 在 microVM 里运行命令，适合不信任命令或干净环境 |

`local` 和 `danger-full-access` 也存在，但它们是显式兼容模式，不是推荐默认模式。

### 从代码角度看：三种后端

后端只回答一个问题：`run_shell` 到底在哪里执行？

| 后端 | 执行位置 | 适合场景 | 主要代价 |
|------|----------|----------|----------|
| `LocalBackend` | 宿主机直接执行 | 兼容、调试、明确全访问 | 没有隔离 |
| `BwrapBackend` | 宿主机工具链 + Linux OS sandbox | 个人 Linux 日常开发默认 | 依赖 bubblewrap，隔离弱于 microVM |
| `MicrosandboxBackend` | microsandbox microVM + OCI image | 不信任命令、强隔离、干净环境 | image 可能缺宿主依赖，体验更重 |

默认选择 `BwrapBackend`，不是因为它隔离最强，而是因为它最适合个人 Linux 日常 coding agent：

```text
能用宿主机现有 Python/Node/Rust/Go/uv/npm/pytest
又能限制命令不要随便写 home、访问网络或越过 workspace
```

`MicrosandboxBackend` 是重要亮点，但不适合作为默认。它更适合“我要强隔离地跑这条命令”而不是“每天都用它跑项目测试”。

`LocalBackend` 必须保留，但它应该被明确标成无沙箱模式。它的价值是兼容和调试，不是安全。

### 用户可见 profile

profile 是用户选择的安全模式。它不是一个后端名，而是一组默认边界。

推荐 profile：

| Profile | Backend | Workspace | Network | Fallback | 用途 |
|---------|---------|-----------|---------|----------|------|
| `workspace` | `bwrap` | 可写 | 关闭 | 默认不回退 | 默认日常开发 |
| `read-only` | `bwrap` | 只读 | 关闭 | 默认不回退 | 代码审查、解释、只读探索 |
| `local` | `local` | 全访问 | 宿主网络 | 不需要 | 兼容和调试 |
| `danger-full-access` | `local` | 全访问 | 宿主网络 | 不需要 | 明确危险的全访问 |
| `microsandbox-dev` | `microsandbox` | 可写挂载 | 默认关闭 | 不回退 | 隔离地跑测试/构建 |
| `microsandbox-safe` | `microsandbox` | 只读挂载 | 关闭 | 不回退 | 运行不信任的只读命令 |
| `microsandbox-strict` | `microsandbox` | 只读挂载 | 关闭 | 禁止回退 | 最保守 microVM 模式 |

这张表是设计的核心。后续新增 `DockerBackend` 或 `RemoteBackend` 时，也应该先问：它服务哪个 profile，而不是直接把后端名暴露给用户。

### policy 到底管什么

policy 不是一个复杂的独立引擎，第一版只是 `SandboxConfig` 里的边界字段。它主要管四件事：

| 边界 | 默认策略 | 原因 |
|------|----------|------|
| workspace | `workspace` 可写，`read-only` 只读 | 个人开发需要能跑格式化、测试生成文件，但审查模式要只读 |
| network | 默认关闭 | 避免模型生成的命令随意下载、上传或访问外网 |
| env/secrets | 默认不转发敏感环境变量 | 避免 API key、SSH key、token 被带进 sandbox |
| protected paths | `.git`、`.env`、`.codex`、`.claude` 等默认保护 | 避免 shell 命令绕过文件工具权限直接改敏感项目状态 |

这里要特别说清楚：第一阶段的 protected paths 只能约束 `run_shell`，不能约束宿主进程里的 `read_file/write_file/edit_file`。文件工具仍然靠权限系统保护。

### 默认行为

默认策略要偏安全，但不能破坏个人 Linux 日常开发体验：

```text
Linux 且 bwrap 可用：
  默认 profile = workspace

Linux 但 bwrap 不可用：
  返回明确错误，提示安装 bubblewrap
  只有用户显式允许 fallback 时才退回 local

非 Linux：
  暂时 profile = local
  明确提示：nano_code 的 OS sandbox 第一阶段只支持 Linux
```

如果用户要强隔离，显式选择：

```bash
nano-code --sandbox microsandbox-safe "inspect this project"
nano-code --sandbox microsandbox-dev --sandbox-image node:22 "run npm test"
```

如果用户要完全兼容，显式选择：

```bash
nano-code --sandbox local "run this host-specific command"
```

默认行为要坚持 fail closed：Linux 上应该优先提示用户安装 `bubblewrap`，而不是悄悄退回 `local`。只有用户显式配置 `allow_fallback_to_local`，才允许从 sandbox 退回无隔离执行。

### 模块结构

建议结构：

```text
nano_code/sandbox/
├── __init__.py
├── types.py
├── config.py
├── manager.py
├── backend.py                 # SandboxBackend 协议 + LocalBackend
├── bwrap_backend.py           # Linux 默认 sandbox 后端
└── microsandbox_backend.py    # microVM 进阶后端
```

第一版只新增 `bwrap_backend.py`，不急着把 `LocalBackend` 从 `backend.py` 拆出去。等后端数量继续增加，再拆 `local_backend.py`。

### 核心运行链路

```text
模型调用 run_shell
  -> ToolRuntime 参数校验
  -> PreToolUse hooks
  -> permission policy
  -> ToolRegistry 调用 run_shell tool
  -> SandboxManager.run_shell()
  -> BwrapBackend / MicrosandboxBackend / LocalBackend
  -> CommandResult.to_tool_output()
  -> tool_result 回到模型
```

权限系统仍在 sandbox 之前。sandbox 模块不直接接触模型消息，不负责确认 UI，不负责 hooks，不负责判断 shell 命令语义。

换句话说，sandbox 的输入应该已经是一个“被允许尝试执行”的 shell 命令。sandbox 只负责把它放进正确的边界里执行，并把 stdout、stderr、exit code、timeout、backend name 返回出去。

### 设计边界

本方案只改变 `run_shell` 的执行后端，不改变工具系统的总体边界：

- `read_file`、`write_file`、`edit_file` 第一阶段仍由宿主进程执行。
- 文件工具靠权限系统保护，不靠 bwrap/microsandbox 保护。
- 模型不能创建、列出、销毁多个 sandbox。
- sandbox 后端不参与模型消息、hooks、权限确认 UI。
- `--yolo` 或其他权限模式不等于自动关闭 sandbox。

这个边界要在 README、`/sandbox` 状态和 threat model 中反复说清楚，避免用户误以为“所有操作都在沙箱里”。

## 详细设计

### 1. `types.py`

新增更清楚的类型。

```python
SandboxBackendName = Literal["local", "bwrap", "microsandbox"]
SandboxProfile = Literal[
    "workspace",
    "read-only",
    "local",
    "danger-full-access",
    "microsandbox-dev",
    "microsandbox-safe",
    "microsandbox-strict",
]
NetworkMode = Literal["none", "default"]
WorkspaceMode = Literal["read-only", "workspace-write", "full-access"]
```

`SandboxConfig` 建议调整为：

```python
@dataclass(frozen=True)
class SandboxConfig:
    profile: SandboxProfile = "workspace"
    backend: SandboxBackendName = "bwrap"
    workspace_host_path: Path | None = None
    workspace_guest_path: str = "/workspace"
    workspace_mode: WorkspaceMode = "workspace-write"
    network_mode: NetworkMode = "none"
    fail_if_unavailable: bool = False
    allow_fallback_to_local: bool = False

    # microsandbox only
    image: str = "python:3.12"
    cpus: int = 2
    memory_mib: int = 2048
    startup_timeout_s: float = 30.0
    command_timeout_s: float = 30.0

    # local/bwrap policy
    protected_paths: tuple[str, ...] = (".git", ".env", ".env.*", ".codex", ".claude")
    extra_writable_roots: tuple[Path, ...] = ()
    forwarded_env: tuple[str, ...] = ()
```

说明：

- `profile` 是用户心智。
- `backend` 是实现细节。
- `workspace_mode` 决定 workspace 可写还是只读。
- `network_mode` 默认 `none`。
- `fail_if_unavailable` 用于 strict profile。
- `allow_fallback_to_local` 必须显式为 true 才能 fallback。
- `forwarded_env` 是 secret policy 的唯一入口。

`CommandResult` 保持当前结构即可：

```python
stdout
stderr
exit_code
timed_out
backend_name
error
```

不要把 streaming、pty、metrics 塞进第一版 `CommandResult`。

### 2. `config.py`

职责：

- 从 CLI args、环境变量、默认值生成 `SandboxConfig`。
- 把 profile 展开成 backend/workspace/network/fallback 策略。
- 做平台检查和参数校验。

建议 CLI：

```bash
--sandbox workspace
--sandbox read-only
--sandbox local
--sandbox danger-full-access
--sandbox microsandbox-dev
--sandbox microsandbox-safe
--sandbox microsandbox-strict

--sandbox-network default|none
--sandbox-image python:3.12
--sandbox-memory 2048
--sandbox-cpus 2
--sandbox-env NAME
--sandbox-extra-write PATH
```

兼容旧参数：

```bash
--sandbox microsandbox
--sandbox-readonly-workspace
--sandbox-no-network
```

兼容策略：

- `--sandbox microsandbox` 映射到 `microsandbox-dev`。
- `--sandbox microsandbox --sandbox-readonly-workspace` 映射到 `microsandbox-safe`。
- `--sandbox-no-network` 覆盖 network 为 `none`。
- `--sandbox-readonly-workspace` 覆盖 workspace 为只读。

环境变量：

```text
NANO_CODE_SANDBOX=workspace|read-only|local|danger-full-access|microsandbox-dev|...
NANO_CODE_SANDBOX_NETWORK=none|default
NANO_CODE_SANDBOX_IMAGE=python:3.12
NANO_CODE_SANDBOX_MEMORY=2048
NANO_CODE_SANDBOX_CPUS=2
NANO_CODE_SANDBOX_ENV=NAME1,NAME2
```

默认逻辑：

```text
Linux:
  profile 默认为 workspace

非 Linux:
  profile 默认为 local
  输出提示：OS sandbox is currently Linux-only in nano_code
```

第一版不要自动修改系统，也不要自动安装 bubblewrap。缺失时给出明确错误。

### 3. `backend.py`

保留最小协议：

```python
class SandboxBackend(Protocol):
    name: str

    async def is_available(self) -> bool: ...
    async def start(self) -> None: ...
    async def run_shell(self, command: str, timeout_ms: int, cwd: str | None = None) -> CommandResult: ...
    async def stop(self) -> None: ...
```

`LocalBackend` 继续用 `subprocess.run(shell=True)`，但它必须被清楚标记：

```text
backend_name = local
is_sandboxed = false
```

不需要额外抽象 `FileBackend`、`NetworkPolicyEngine`、`SandboxSession`。这些概念现在还没有足够复杂度。

### 4. `bwrap_backend.py`

这是默认 Linux sandbox 后端。

目标：

```text
使用宿主工具链执行命令，但通过 bubblewrap 限制文件系统和网络边界。
```

第一版能力：

- 使用 `bwrap` 执行命令。
- workspace 可写或只读。
- 网络默认关闭。
- home 默认不挂载。
- `/tmp` 使用 tmpfs。
- `/proc`、`/dev` 提供必要最小挂载。
- 允许读取宿主系统工具链所需目录。
- 支持当前 cwd 映射。
- protected paths 尽量只读或拒绝。

推荐命令模型：

```text
bwrap
  --die-with-parent
  --new-session
  --unshare-pid
  --unshare-ipc
  --unshare-uts
  --unshare-cgroup-try
  --unshare-net                 # network none 时
  --proc /proc
  --dev /dev
  --tmpfs /tmp
  --ro-bind /usr /usr
  --ro-bind /bin /bin
  --ro-bind /lib /lib
  --ro-bind /lib64 /lib64       # 存在才加
  --ro-bind /etc /etc
  --bind /path/workspace /path/workspace
  --chdir /path/workspace/subdir
  -- /bin/sh -lc "<command>"
```

注意：上面是设计方向，不要机械照抄。不同发行版目录可能不同，代码应“存在才挂载”。

workspace 模式：

```text
workspace:
  workspace bind 可写
  protected paths 只读或遮蔽

read-only:
  workspace ro-bind

danger-full-access/local:
  不走 bwrap
```

protected path 策略：

第一版建议务实：

```text
workspace 可写时：
  .git        尽量 ro-bind 回原路径
  .codex      尽量 ro-bind
  .claude     尽量 ro-bind
  .env        如果是文件，ro-bind；如果需要更强，后续改为遮蔽
  .env.*      同上
```

如果 bwrap 无法对某个 protected path 单独只读绑定：

- 不要静默忽略。
- 记录 warning。
- `microsandbox-strict` 或 `read-only` 下 fail closed。
- `workspace` 下可以继续，但 status 要显示 protected path 未完全隔离。

网络：

```text
network none:
  加 --unshare-net

network default:
  不加 --unshare-net
```

第一版不做域名 allowlist，不做 DNS rebinding 防护。那是后续扩展。

环境变量：

默认传递最小环境：

```text
PATH
HOME=/tmp/nano-code-home 或 /tmp
LANG
LC_ALL
TERM
```

不要默认传：

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
AWS_*
GITHUB_TOKEN
NPM_TOKEN
SSH_AUTH_SOCK
```

用户通过 `--sandbox-env NAME` 显式白名单传入。

### 5. `microsandbox_backend.py`

保留当前后端，但定位改为进阶模式。

职责：

- 启动 microVM。
- 用 OCI image 提供隔离环境。
- 挂载 workspace 到 `/workspace`。
- 支持只读 workspace。
- 支持 `Network.none()`。
- 控制 CPU、内存、启动 timeout、命令 timeout。
- 会话结束 stop。

不做：

- 不暴露 create/list/inspect/metrics 给模型。
- 不让模型自己管理多个 sandbox。
- 不使用 guest 文件系统 API 执行 `read_file/write_file/edit_file`。
- 不做 detached long-running sandbox。
- 不承诺启动性能指标。

microsandbox profile：

```text
microsandbox-dev:
  writable_workspace = true
  network_mode = none
  fail_if_unavailable = true

microsandbox-safe:
  writable_workspace = false
  network_mode = none
  fail_if_unavailable = true

microsandbox-strict:
  writable_workspace = false
  network_mode = none
  fail_if_unavailable = true
  allow_fallback_to_local = false
```

是否允许 `microsandbox-dev` 网络：

- 默认不允许。
- 用户显式 `--sandbox-network default` 才允许。

### 6. `manager.py`

`SandboxManager` 保持会话级，不做全局单例。

职责：

- 根据 `SandboxConfig` 创建后端。
- 懒启动。
- 复用会话内 backend。
- 路径映射。
- 处理 backend 不可用和 fallback。
- stop 清理。
- 提供 status。

新增：

```python
def describe(self) -> SandboxStatus
```

或先简单返回字符串：

```python
def describe(self) -> str
```

状态展示示例：

```text
Sandbox profile: workspace
Backend: bwrap
Shell isolation: OS-level sandbox
Workspace: /root/EvoCode/nano_code
Workspace writable: true
Network: none
Home mounted: false
Protected paths: .git, .env, .codex, .claude
Secrets: host env not forwarded; allowlist empty
Fallback to local: false
```

backend 不可用策略：

```text
workspace/read-only:
  bwrap 不可用 -> 返回明确错误，提示安装 bubblewrap
  如果 allow_fallback_to_local=true 才 fallback

microsandbox-*:
  microsandbox SDK 或 runtime 不可用 -> fail closed

local/danger-full-access:
  永远可用
```

不要在 manager 里直接打印太多内容。错误返回给 tool result，启动 status 由 CLI/UI 决定是否展示。

### 7. CLI 和 UI

CLI 参数建议：

```bash
nano-code --sandbox workspace "run tests"
nano-code --sandbox read-only "inspect this repo"
nano-code --sandbox local "run this local-only command"
nano-code --sandbox microsandbox-safe "run this untrusted command"
nano-code --sandbox microsandbox-dev --sandbox-image node:22 "run npm test"
```

启动或首次 shell 执行前可以显示一次简短 status：

```text
Sandbox: workspace (bwrap, network=none, workspace-write)
```

REPL 可后续加：

```text
/sandbox
```

显示完整 boundary。第一版可以先在 `--help` 和启动时说明，不急着加 REPL 命令。

## Threat Model

### 保护对象

```text
宿主 home
SSH key
API key
.env
Git metadata
Codex/Claude/Nano Code 配置
系统目录
本地网络服务
私有网络资源
```

### 不可信输入

```text
模型生成的 shell 命令
项目中的 package scripts
测试脚本
构建脚本
第三方依赖安装脚本
从网络下载的脚本
工具输出中的 prompt injection
```

### 安全边界

```text
ToolRuntime:
  执行前权限、hooks、确认

BwrapBackend:
  限制 shell 命令在宿主工具链上的文件系统和网络访问

MicrosandboxBackend:
  用 microVM 隔离 shell 命令和子进程

PermissionPolicy:
  对所有工具生效，包括 read/edit/write/run_shell/MCP
```

### 非目标

第一阶段不承诺：

- 完整抵御恶意内核级攻击。
- 完整网络域名策略。
- 所有文件工具都在 sandbox 中执行。
- 任务级临时 workspace 和 diff apply。
- 多 sandbox 编排。
- metrics、端口 inspection、detached sandbox。
- 自动安装 bubblewrap 或 microsandbox。

## 硬性约束

- 默认 Linux 模式不能直接裸跑 shell。
- 所有工具必须先经过权限和 hooks。
- 模型不能直接管理 sandbox 生命周期。
- sandbox 只处理 `run_shell`。
- 文件工具第一阶段仍在宿主执行。
- 网络默认关闭。
- 宿主 home 默认不挂载。
- 宿主 secret 环境变量默认不传递。
- fallback 到 local 必须显式开启。
- `microsandbox-strict` 不允许 fallback。
- protected paths 不能静默失效；至少要在 status/warning 中暴露。
- 不新增复杂依赖；`bwrap` 是系统工具，不是 Python 包依赖。
- 不引入任务调度器、事件总线、资源池。
- 不做跨平台承诺；本方案主要面向 Linux。

## 隐含要求

- 日常开发要能用宿主工具链。
- 安全边界必须用户看得懂。
- profile 名称要表达风险等级。
- sandbox 错误要可恢复、可解释。
- 不能因为 sandbox 导致权限系统绕过。
- 不能让用户误以为 microsandbox 默认等于完整安全。
- writable workspace 风险必须明确说明。
- read-only 模式下测试可能失败，这是可接受行为。
- 设计应允许未来新增 `DockerBackend`、`RemoteBackend`，但不要提前抽象过度。

## 不能做什么

- 不能默认 `danger-full-access`。
- 不能默认传 API key 到 sandbox。
- 不能把 `.env` 内容注入模型或 sandbox。
- 不能把整个 home bind 进 sandbox。
- 不能让 bwrap 不可用时静默裸跑。
- 不能让 microsandbox 不可用时静默回 local。
- 不能把 sandbox profile 和 permission mode 混成一个概念。
- 不能为了追求“完整隔离”把 read/write/edit 全部迁进 sandbox，第一阶段不做 full workspace sandbox。
- 不能暴露 sandbox 管理工具给模型，让模型创建多个 VM。
- 不能宣传“实现了 Codex sandbox”或“实现了 microsandbox 平台”。只能说借鉴其设计思路和封装其能力。

## 可能踩坑

### bwrap 参数在不同发行版上差异很大

`/lib64`、`/usr/lib64`、动态链接器路径可能不同。代码应按路径存在与否添加 bind，不要写死单一发行版。

### 宿主工具链依赖 home

`npm`、`pip`、`cargo` 可能读 home 下缓存和配置。默认不挂 home 会更安全，但可能导致命令失败。解决方式是：

- 默认安全。
- 错误清晰。
- 允许用户通过 profile 或 extra root 显式授权。

### workspace writable 会绕过文件工具保护

如果 shell 在 workspace 中可写，它可以直接修改 `.git`、`.env` 等路径。必须实现 protected path 或至少清楚提示风险。

### 网络关闭导致构建失败

很多测试会临时下载依赖。默认网络关闭是安全选择，但用户需要知道如何显式打开。

### microsandbox image 缺依赖

`python:3.12` 里没有 Node、Rust、系统库。microsandbox 不适合作为日常默认后端。它是强隔离模式。

### cwd 映射

bwrap 使用宿主路径，microsandbox 使用 guest 路径 `/workspace`。`SandboxManager` 必须把路径映射封装好，工具层不应该知道这些差异。

### timeout 和进程清理

本地 `subprocess.run(timeout=...)` 和 bwrap/microsandbox timeout 行为不同。统一返回 `CommandResult(timed_out=True)`，不要泄漏 backend 异常细节。

### fallback 语义

`auto` 或 fallback 很危险。用户以为在 sandbox，实际裸跑，会破坏信任。fallback 必须显式，并在 status 中显示。

### protected path overlay 复杂

对文件做 ro-bind、遮蔽、tmpfs overlay 都有边界。第一版选择最简单可解释实现，不追求完美文件系统策略。

### permission 和 sandbox 语义混乱

`--yolo` 不应该自动等于无 sandbox。用户可以选择不问批准，但仍保留 sandbox。把这两个维度分开。

## 实施步骤

### 第一阶段：profile 和状态

1. 修改 `types.py`，引入 `SandboxProfile`、`SandboxBackendName`、`WorkspaceMode`。
2. 修改 `config.py`，支持新 profile，并兼容旧参数。
3. 给 `SandboxManager` 增加 `describe()`。
4. CLI help 中说明 profile。
5. 默认 Linux profile 改为 `workspace`，但如果 bwrap 尚未实现，可以先保持 local，并在文档中标注目标状态。

### 第二阶段：BwrapBackend

1. 新增 `bwrap_backend.py`。
2. 检查 `bwrap` 可用性。
3. 实现 read-only/workspace-write。
4. 实现 network none。
5. 实现最小 env。
6. 实现 cwd 映射。
7. 实现 timeout 和 `CommandResult` 转换。
8. 处理 bwrap 缺失错误。

### 第三阶段：secret 和 protected path

1. 默认不转发敏感 env。
2. 支持 `--sandbox-env NAME` 白名单。
3. protected paths 先做 `.git`、`.env`、`.env.*`、`.codex`、`.claude`。
4. status 显示 protected path 是否完全生效。
5. 文档写清 writable workspace 风险。

### 第四阶段：microsandbox profile 化

1. 把现有 `--sandbox microsandbox` 映射到 `microsandbox-dev`。
2. 增加 `microsandbox-safe` 和 `microsandbox-strict`。
3. 默认 microsandbox 网络关闭。
4. 明确不 fallback。
5. status 显示 image、CPU、内存、workspace mount。

### 第五阶段：后续扩展

后续再考虑：

- `/sandbox` REPL 命令。
- network allowlist。
- bwrap extra writable roots。
- Linux integration tests。
- cold start / warm reuse benchmark。
- Docker backend。
- full workspace sandbox。

## 验收标准

- Linux 默认 sandbox profile 不裸跑 shell。
- bwrap 不可用时有明确错误，不静默 fallback。
- `local` 和 `danger-full-access` 明确显示无隔离。
- `workspace` 使用宿主工具链，workspace 可写，网络关闭。
- `read-only` 使用宿主工具链，workspace 只读，网络关闭。
- `microsandbox-safe` 使用 microVM，workspace 只读，网络关闭。
- `microsandbox-dev` 使用 microVM，workspace 可写，网络关闭。
- host env secrets 默认不传入 sandbox。
- 用户可显式 allowlist env。
- status 能说明 backend、network、workspace、secret、fallback。
- ToolRuntime 权限链路不变。
- 文件工具仍在宿主执行，并在 threat model 中明确说明。

## 推荐最终表述

可以在 README 或面试中这样描述：

```text
nano_code 的 sandbox 采用两级后端设计。默认 Linux 后端参考 Codex CLI 的本地 sandbox 思路：命令使用宿主工具链执行，但通过 OS 级隔离限制在 workspace 内，网络默认关闭。microsandbox 作为进阶后端，用 microVM 和 OCI image 提供更强隔离，适合运行不信任命令或需要干净环境的场景。权限系统始终在 sandbox 之前生效，sandbox 只负责 run_shell 的执行边界。
```

不要说：

```text
nano_code 实现了 Codex sandbox
nano_code 完整复刻 microsandbox
所有工具都在 sandbox 中执行
```

更准确的是：

```text
nano_code 借鉴 Codex 的 sandbox/profile/approval 分层思想，并把 microsandbox 封装为可替换的 shell execution backend。
```
