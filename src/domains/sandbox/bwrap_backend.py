"""bubblewrap-backed Linux command execution."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .types import CommandResult, SandboxConfig


class BwrapBackend:
    name = "bwrap"

    def __init__(self, config: SandboxConfig):
        self.config = config

    async def is_available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("bwrap") is not None

    async def start(self) -> None:
        if not await self.is_available():
            raise RuntimeError(
                "bubblewrap is not available. Install `bubblewrap` or use `--sandbox local` "
                "for explicit host execution."
            )

    async def run_shell(self, command: str, timeout_ms: int, cwd: str | None = None) -> CommandResult:
        timeout_s = timeout_ms / 1000
        try:
            argv = self.build_argv(command, cwd)
        except Exception as e:
            return CommandResult(error=str(e), backend_name=self.name)

        def _run() -> CommandResult:
            try:
                result = subprocess.run(
                    argv,
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
                return CommandResult(
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                    exit_code=result.returncode,
                    backend_name=self.name,
                )
            except subprocess.TimeoutExpired:
                return CommandResult(timed_out=True, backend_name=self.name)
            except Exception as e:
                return CommandResult(error=str(e), backend_name=self.name)

        return await asyncio.to_thread(_run)

    async def stop(self) -> None:
        return None

    def build_argv(self, command: str, cwd: str | None = None) -> list[str]:
        workspace = self.config.resolved_workspace_host_path()
        cwd_path = Path(cwd or workspace).resolve()
        try:
            cwd_path.relative_to(workspace)
        except ValueError:
            raise ValueError(f"cwd is outside sandbox workspace: {cwd_path}") from None

        argv = [
            "bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
        ]
        if self.config.network_mode == "none":
            argv.append("--unshare-net")

        argv.extend(["--clearenv", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])
        argv.extend(["--dir", "/tmp/nanocode-home"])

        for name, value in self._sandbox_env().items():
            argv.extend(["--setenv", name, value])

        for path in self._system_roots():
            argv.extend(["--ro-bind", path, path])

        argv.extend(self._dir_args_for_parent(workspace))
        workspace_flag = "--ro-bind" if self.config.workspace_mode == "read-only" else "--bind"
        argv.extend([workspace_flag, str(workspace), str(workspace)])

        if self.config.workspace_mode == "workspace-write":
            for protected in self._existing_protected_paths(workspace):
                argv.extend(["--ro-bind", str(protected), str(protected)])

        for extra_root in self.config.extra_writable_roots:
            root = extra_root.resolve()
            if not root.exists():
                raise ValueError(f"sandbox extra writable root does not exist: {root}")
            argv.extend(self._dir_args_for_parent(root))
            argv.extend(["--bind", str(root), str(root)])

        argv.extend(["--chdir", str(cwd_path), "--", "/bin/sh", "-lc", command])
        return argv

    def _sandbox_env(self) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": "/tmp/nanocode-home",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": os.environ.get("TERM", "dumb"),
        }
        if os.environ.get("LC_ALL"):
            env["LC_ALL"] = os.environ["LC_ALL"]
        for name in self.config.forwarded_env:
            if name in os.environ:
                env[name] = os.environ[name]
        return env

    def _system_roots(self) -> list[str]:
        roots = [
            "/usr",
            "/bin",
            "/sbin",
            "/lib",
            "/lib64",
            "/usr/lib64",
            "/etc",
        ]
        return [path for path in roots if Path(path).exists()]

    def _existing_protected_paths(self, workspace: Path) -> list[Path]:
        protected: list[Path] = []
        seen: set[Path] = set()
        for pattern in self.config.protected_paths:
            matches = workspace.glob(pattern) if any(char in pattern for char in "*?[") else [workspace / pattern]
            for path in matches:
                if not path.exists():
                    continue
                resolved = path.resolve()
                try:
                    resolved.relative_to(workspace)
                except ValueError:
                    continue
                if resolved not in seen:
                    protected.append(resolved)
                    seen.add(resolved)
        return protected

    def _dir_args_for_parent(self, path: Path) -> list[str]:
        args: list[str] = []
        current = Path("/")
        for part in path.parent.parts[1:]:
            current = current / part
            args.extend(["--dir", str(current)])
        return args


__all__ = ["BwrapBackend"]
