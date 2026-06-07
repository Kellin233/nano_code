"""Command hook process execution."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .types import HookCommand, HookInput, HookOutput


async def run_command_hook(hook: HookCommand, hook_input: HookInput) -> HookOutput:
    payload = json.dumps(hook_input.as_dict())
    try:
        process = await asyncio.create_subprocess_shell(
            hook.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path(hook_input.cwd)),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload.encode()),
                timeout=hook.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return HookOutput(
                action="deny" if hook.fail_closed else "allow",
                error=f"hook timed out after {hook.timeout_ms}ms",
            )

        text = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if not text:
            return HookOutput(
                action="deny" if hook.fail_closed and process.returncode else "allow",
                error=err,
            )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return HookOutput(
                action="deny" if hook.fail_closed else "allow",
                error=f"hook returned non-JSON output: {text[:200]}",
            )
        return HookOutput(
            action=parsed.get("action", "allow"),
            reason=parsed.get("reason", ""),
            updated_input=parsed.get("updated_input"),
            content=parsed.get("content", ""),
        )
    except Exception as exc:
        return HookOutput(action="deny" if hook.fail_closed else "allow", error=str(exc))

