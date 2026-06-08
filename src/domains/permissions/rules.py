"""Settings-backed allow/deny permission rules."""

from __future__ import annotations

import json
import re
from pathlib import Path


_cached_rules: dict | None = None


def _parse_rule(rule: str) -> dict:
    match = re.match(r"^([a-zA-Z0-9_-]+(?:__[a-zA-Z0-9_-]+)*)\((.+)\)$", rule)
    if match:
        return {"tool": match.group(1), "pattern": match.group(2)}
    return {"tool": rule, "pattern": None}


def _matches_tool(rule_tool: str, tool_name: str) -> bool:
    if rule_tool == tool_name:
        return True
    return rule_tool.startswith("mcp__") and tool_name.startswith(rule_tool + "__")


def _load_settings(file_path: Path) -> dict | None:
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text())
    except Exception:
        return None


def load_permission_rules() -> dict:
    global _cached_rules
    if _cached_rules is not None:
        return _cached_rules

    allow: list[dict] = []
    deny: list[dict] = []
    for path in (Path.home() / ".claude" / "settings.json", Path.cwd() / ".claude" / "settings.json"):
        settings = _load_settings(path)
        if not settings or "permissions" not in settings:
            continue
        permissions = settings["permissions"]
        allow.extend(_parse_rule(rule) for rule in permissions.get("allow", []))
        deny.extend(_parse_rule(rule) for rule in permissions.get("deny", []))

    _cached_rules = {"allow": allow, "deny": deny}
    return _cached_rules


def reset_permission_cache() -> None:
    global _cached_rules
    _cached_rules = None


def matches_rule(rule: dict, tool_name: str, inp: dict) -> bool:
    if not _matches_tool(rule["tool"], tool_name):
        return False
    if rule["pattern"] is None:
        return True

    value = ""
    if tool_name == "run_shell":
        value = inp.get("command", "")
    elif "file_path" in inp:
        value = inp["file_path"]
    else:
        return True

    pattern = rule["pattern"]
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return value == pattern


def rule_decision(tool_name: str, inp: dict) -> str | None:
    rules = load_permission_rules()
    for rule in rules["deny"]:
        if matches_rule(rule, tool_name, inp):
            return "deny"
    for rule in rules["allow"]:
        if matches_rule(rule, tool_name, inp):
            return "allow"
    return None
