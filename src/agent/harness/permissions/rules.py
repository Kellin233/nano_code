"""Settings-backed allow/deny permission rules."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

Rule = dict[str, str | None]
PermissionRules = dict[str, list[Rule]]

_cached_rules: PermissionRules | None = None


def _parse_rule(rule: str) -> dict[str, str | None]:
    match = re.match(r"^([a-zA-Z0-9_-]+(?:__[a-zA-Z0-9_-]+)*)\((.+)\)$", rule)
    if match:
        return {"tool": match.group(1), "pattern": match.group(2)}
    return {"tool": rule, "pattern": None}


def _matches_tool(rule_tool: str, tool_name: str) -> bool:
    if rule_tool == tool_name:
        return True
    return rule_tool.startswith("mcp__") and tool_name.startswith(rule_tool + "__")


def _load_settings(file_path: Path) -> dict[str, Any] | None:
    if not file_path.exists():
        return None
    try:
        data = json.loads(file_path.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_permission_rules() -> PermissionRules:
    global _cached_rules
    if _cached_rules is not None:
        return _cached_rules

    allow: list[Rule] = []
    deny: list[Rule] = []
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


def _normalize_path_text(path: str) -> str:
    text = str(path or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def _resolve_rule_path(path: str, cwd: Path | None) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    return ((cwd or Path.cwd()) / candidate).resolve(strict=False)


def _path_matches_pattern(value: str, pattern: str, cwd: Path | None) -> bool:
    value_text = _normalize_path_text(value)
    pattern_text = _normalize_path_text(pattern)
    if not value_text or not pattern_text:
        return False

    wildcard = pattern_text.endswith("*")
    pattern_prefix = pattern_text[:-1] if wildcard else pattern_text

    if wildcard:
        value_abs = _normalize_path_text(str(_resolve_rule_path(value_text, cwd)))
        pattern_abs = _normalize_path_text(str(_resolve_rule_path(pattern_prefix, cwd)))
        return value_text.startswith(pattern_prefix) or value_abs.startswith(pattern_abs)

    value_path = _resolve_rule_path(value_text, cwd)
    pattern_path = _resolve_rule_path(pattern_prefix, cwd)

    return value_path == pattern_path


def matches_rule(rule: Rule, tool_name: str, inp: dict, *, cwd: Path | None = None) -> bool:
    rule_tool = rule.get("tool") or ""
    if not _matches_tool(rule_tool, tool_name):
        return False
    if rule["pattern"] is None:
        return True

    if tool_name == "run_shell":
        value = str(inp.get("command", ""))
        pattern = rule["pattern"]
        if pattern.endswith("*"):
            return value.startswith(pattern[:-1])
        return value == pattern

    elif "file_path" in inp:
        value = str(inp["file_path"])
        return _path_matches_pattern(value, rule["pattern"], cwd)
    else:
        return True



def rule_decision(tool_name: str, inp: dict, *, cwd: Path | None = None) -> str | None:
    rules = load_permission_rules()
    for rule in rules["deny"]:
        if matches_rule(rule, tool_name, inp, cwd=cwd):
            return "deny"
    for rule in rules["allow"]:
        if matches_rule(rule, tool_name, inp, cwd=cwd):
            return "allow"
    return None
