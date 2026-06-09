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
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

logger = logging.getLogger(__name__)

NANOCODE_INSTALL_SCRIPT = """
pip install nanocode 2>/dev/null || pip install --no-deps nanocode 2>/dev/null || true
pip install anthropic openai rich prompt_toolkit 2>/dev/null || true
"""


class NanoCodeAgent(BaseAgent):
    """Terminal-Bench agent that delegates to NanoCode CLI."""

    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(self, logs_dir: Path, model_name: str | None = None, **kwargs):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self._agent_env = kwargs.get("agent_env", {})

    @staticmethod
    def name() -> str:
        return "nanocode"

    def version(self) -> str | None:
        try:
            from nanocode import __version__
            return __version__
        except ImportError:
            return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        """Install NanoCode and dependencies in the Docker environment."""
        self.logger.info("Setting up NanoCode agent...")

        # Install from GitHub (PyPI 暂不可用)
        repo_url = os.environ.get("NANOCODE_REPO_URL", "https://github.com/Kellin233/nano_code.git")
        result = await environment.exec(
            f"pip install git+{repo_url} 2>&1 | tail -5",
            timeout=120,
        )
        self.logger.info(f"Install NanoCode: {result.stdout}")

        # 解析模型名（harbor 传 "openai/deepseek-chat" 或 "anthropic/claude-sonnet-4-6"）
        model_full = self.model_name or "anthropic/claude-sonnet-4-6"
        provider, model_name = model_full.split("/", 1) if "/" in model_full else ("anthropic", model_full)

        # 按 provider 设置环境变量
        if provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY", "")
            api_base = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
            env_setup = (
                f"export OPENAI_API_KEY='{api_key}'\n"
                f"export OPENAI_BASE_URL='{api_base}'\n"
                f"export NANO_CODE_MODEL='{model_name}'\n"
            )
        else:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            api_base = os.environ.get("ANTHROPIC_BASE_URL", "")
            env_setup = f"export ANTHROPIC_API_KEY='{api_key}'\n"
            if api_base:
                env_setup += f"export ANTHROPIC_BASE_URL='{api_base}'\n"
            env_setup += f"export NANO_CODE_MODEL='{model_name}'\n"

        await environment.exec(env_setup)
        self.logger.info(f"Setup complete ({provider}/{model_name})")

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """Run NanoCode on the task instruction."""
        self.logger.info(f"Running NanoCode: {instruction[:100]}...")

        # Escape the instruction for shell
        safe_instruction = instruction.replace("'", "'\"'\"'")

        model_name = self.model_name.split("/", 1)[1] if "/" in (self.model_name or "") else (self.model_name or "")
        model_flag = f"--model {model_name}" if model_name else ""
        result = await environment.exec(
            f"nanocode --yolo --max-turns 30 --max-cost 5.00 {model_flag} '{safe_instruction}'",
            timeout=600,
        )

        output = result.stdout + "\n" + result.stderr
        self.logger.info(f"NanoCode output: {len(output)} chars, exit={result.exit_code}")

        # Store results in context
        context.agent_output = output
        context.exit_code = result.exit_code

        # Try to extract structured results
        try:
            json_match = re.search(r'\{[\s\S]*"result"[\s\S]*\}', output)
            if json_match:
                context.structured_output = json.loads(json_match.group(0))
        except Exception:
            pass
