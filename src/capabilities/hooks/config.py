"""Hook configuration loading and matching."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .runner import run_command_hook
from .types import HookCommand, HookEventName, HookInput, HookOutput


class HookManager:
    def __init__(self, hooks: list[HookCommand] | None = None):
        self._hooks = hooks or []

    @classmethod
    def capture(cls, *, include_project_hooks: bool | None = None) -> HookManager:
        include_project = (
            os.environ.get("NANO_CODE_TRUST_PROJECT_HOOKS") == "1"
            if include_project_hooks is None
            else include_project_hooks
        )
        hooks: list[HookCommand] = []
        hooks.extend(_load_hooks(Path.home() / ".claude" / "settings.json"))
        if include_project:
            hooks.extend(_load_hooks(Path.cwd() / ".claude" / "settings.json"))
        return cls(hooks)

    def has_hooks(self, event: HookEventName) -> bool:
        return any(hook.event == event for hook in self._hooks)

    async def run(self, event: HookEventName, hook_input: HookInput) -> list[HookOutput]:
        matches = [
            hook for hook in self._hooks
            if hook.event == event and _matches(hook.matcher, hook_input)
        ]
        if not matches:
            return []
        outputs: list[HookOutput] = []
        for hook in matches:
            outputs.append(await run_command_hook(hook, hook_input))
        return outputs


def _load_hooks(path: Path) -> list[HookCommand]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return []
    config = raw.get("hooks")
    if not isinstance(config, dict):
        return []

    hooks: list[HookCommand] = []
    for event, items in config.items():
        if event not in {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("command"):
                continue
            hooks.append(HookCommand(
                event=event,
                command=str(item["command"]),
                matcher=str(item.get("matcher") or "*"),
                timeout_ms=int(item.get("timeout_ms") or 3000),
                fail_closed=bool(item.get("fail_closed")),
            ))
    return hooks


def _matches(matcher: str, hook_input: HookInput) -> bool:
    if matcher in {"", "*"}:
        return True
    if hook_input.tool_name:
        return matcher == hook_input.tool_name
    return False

