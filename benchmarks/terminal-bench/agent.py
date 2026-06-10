"""NanoCode agent adapter for Harbor/Terminal-Bench.

Implements the BaseAgent interface so NanoCode can be evaluated
on Terminal-Bench tasks.

Usage:
  harbor run --agent-import-path benchmarks/terminal-bench/agent.py:NanoCodeAgent \
             --model openai/gpt-5.5 \
             --dataset terminal-bench@2.0
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

logger = logging.getLogger(__name__)


class NanoCodeAgent(BaseAgent):
    """Terminal-Bench agent that delegates to NanoCode CLI."""

    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self._extra_env = dict(extra_env or {})

    @staticmethod
    def name() -> str:
        return "nanocode"

    def version(self) -> str | None:
        try:
            from nanocode import __version__

            return __version__
        except ImportError:
            return "1.0.0"

    def _model_parts(self) -> tuple[str, str]:
        model_full = self.model_name or "openai/deepseek-v4-pro"
        if "/" in model_full:
            return model_full.split("/", 1)
        return "anthropic", model_full

    def _runtime_env(self) -> dict[str, str]:
        provider, model_name = self._model_parts()
        env = dict(self._extra_env)
        env["NANO_CODE_MODEL"] = model_name

        if provider == "openai":
            env["OPENAI_API_KEY"] = env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
            base_url = env.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
            if base_url:
                env["OPENAI_BASE_URL"] = base_url
        else:
            env["ANTHROPIC_API_KEY"] = env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
            base_url = env.get("ANTHROPIC_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
            if base_url:
                env["ANTHROPIC_BASE_URL"] = base_url
        return {key: value for key, value in env.items() if value is not None}

    def _redact(self, text: str) -> str:
        if not text:
            return text
        redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[redacted]", text)
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
            secret = self._runtime_env().get(key) or os.environ.get(key)
            if secret and len(secret) >= 8:
                redacted = redacted.replace(secret, "[redacted]")
        return redacted

    async def _exec_checked(
        self,
        environment: BaseEnvironment,
        command: str,
        *,
        label: str,
        env: dict[str, str] | None = None,
        timeout_sec: int = 300,
    ):
        result = await environment.exec(command, env=env, timeout_sec=timeout_sec)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        output = (stdout + "\n" + stderr).strip()
        if result.return_code != 0:
            raise RuntimeError(
                self._redact(f"{label} failed with rc={result.return_code}.\nCommand: {command}\nOutput:\n{output}")
            )
        if output:
            self.logger.info("%s: %s", label, self._redact(output[-1000:]))
        return result

    @staticmethod
    def _source_root() -> Path:
        return Path(os.environ.get("NANOCODE_SOURCE_DIR", Path(__file__).resolve().parents[2])).resolve()

    @staticmethod
    def _copy_source_snapshot(source_dir: Path, target_dir: Path) -> None:
        def ignore(directory: str, names: list[str]) -> set[str]:
            ignored = {
                name
                for name in names
                if name
                in {
                    ".git",
                    ".mypy_cache",
                    ".pytest_cache",
                    ".ruff_cache",
                    ".venv",
                    "venv",
                    "__pycache__",
                    "build",
                    "dist",
                    "jobs",
                    "nanocode.egg-info",
                }
            }
            path = Path(directory)
            if path.name == "benchmarks":
                ignored.add("API.txt")
            if path.name == "swebench":
                ignored.update({"logs", "predictions.json"})
            if path.name == "terminal-bench":
                ignored.add("__pycache__")
            return ignored

        shutil.copytree(source_dir, target_dir, ignore=ignore)

    async def setup(self, environment: BaseEnvironment) -> None:
        """在 Docker 容器中安装 NanoCode + 配置环境变量。"""
        self.logger.info("Setting up NanoCode agent...")
        repo_url = os.environ.get("NANOCODE_REPO_URL")
        apt_packages = "python3 python3-pip ca-certificates"
        if repo_url:
            apt_packages += " git"

        await self._exec_checked(
            environment,
            (
                "if command -v python3 >/dev/null 2>&1 && command -v pip3 >/dev/null 2>&1; "
                "then exit 0; fi; "
                "if command -v apt-get >/dev/null 2>&1; then "
                "APT_OPTS='-o Acquire::ForceIPv4=true -o Acquire::Retries=3 "
                "-o Acquire::http::Timeout=45 -o Acquire::https::Timeout=45'; "
                "DEBIAN_FRONTEND=noninteractive apt-get $APT_OPTS update -qq && "
                "DEBIAN_FRONTEND=noninteractive apt-get $APT_OPTS install -y -qq --no-install-recommends "
                f"{apt_packages}; "
                "else echo 'python3/pip missing and apt-get is unavailable'; exit 1; fi"
            ),
            label="Install Python",
            timeout_sec=900,
        )

        pip_env = {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PIP_INDEX_URL": os.environ.get("PIP_INDEX_URL", "https://pypi.tuna.tsinghua.edu.cn/simple"),
        }

        if repo_url:
            install_target = shlex.quote(f"git+{repo_url}")
            await self._exec_checked(
                environment,
                f"python3 -m pip install --break-system-packages {install_target}",
                label="Install NanoCode from Git",
                env=pip_env,
                timeout_sec=900,
            )
        else:
            source_dir = self._source_root()
            if not (source_dir / "pyproject.toml").exists():
                raise RuntimeError(f"NanoCode source root missing pyproject.toml: {source_dir}")

            await self._exec_checked(
                environment,
                "rm -rf /tmp/nanocode-src && mkdir -p /tmp/nanocode-src",
                label="Prepare NanoCode source directory",
                timeout_sec=60,
            )
            with tempfile.TemporaryDirectory(prefix="nanocode-harbor-src-") as temp_dir:
                snapshot = Path(temp_dir) / "nanocode"
                self._copy_source_snapshot(source_dir, snapshot)
                await environment.upload_dir(snapshot, "/tmp/nanocode-src")
            await self._exec_checked(
                environment,
                "python3 -m pip install --break-system-packages -e /tmp/nanocode-src",
                label="Install NanoCode from local checkout",
                env=pip_env,
                timeout_sec=900,
            )

        await self._exec_checked(
            environment,
            "command -v nanocode && nanocode --help >/tmp/nanocode-help.txt",
            label="Verify NanoCode CLI",
            timeout_sec=120,
        )

        provider, model_name = self._model_parts()
        self.logger.info(f"Setup complete ({provider}/{model_name})")

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """在 Docker 容器中运行 NanoCode 完成任务。"""
        self.logger.info(f"Running: {instruction[:100]}...")

        provider, model_short = self._model_parts()
        runtime_env = self._runtime_env()
        cmd = [
            "nanocode",
            "--yolo",
            "--sandbox",
            "local",
            "--max-turns",
            "30",
            "--max-cost",
            "5.00",
            "--model",
            model_short,
        ]
        if provider == "openai" and runtime_env.get("OPENAI_BASE_URL"):
            cmd.extend(["--api-base", runtime_env["OPENAI_BASE_URL"]])
        cmd.append(instruction)
        command = " ".join(shlex.quote(part) for part in cmd)

        result = await environment.exec(
            command,
            env=runtime_env,
            timeout_sec=600,
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        output = stdout + "\n" + stderr
        self.logger.info(f"Output: {len(output)} chars, rc={result.return_code}")

        # 存入 context（AgentContext 使用 metadata dict 存自定义数据）
        context.metadata = {
            "output": output,
            "return_code": result.return_code,
            "model": model_short,
            "provider": provider,
        }

        log_path = self.logs_dir / "nanocode-output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(self._redact(output), encoding="utf-8")

        if result.return_code != 0:
            raise RuntimeError(
                self._redact(f"NanoCode CLI failed with rc={result.return_code}. See {log_path} for stdout/stderr.")
            )

        # 尝试提取结构化结果
        try:
            json_match = re.search(r'\{[\s\S]*"result"[\s\S]*\}', output)
            if json_match:
                context.metadata["structured_output"] = json.loads(json_match.group(0))
        except Exception:
            pass
