"""Skill 发现和元数据解析。

本模块负责扫描用户级和项目级 skill 目录，解析 `SKILL.md` frontmatter，
形成轻量的 `SkillDefinition`。发现阶段只读取 metadata，不读取正文，从而保证
skill 正文和 supporting files 都在真正调用时才进入上下文。
"""

from __future__ import annotations

import json
from pathlib import Path

from ....agent.harness.context.sources import parse_frontmatter
from .types import SkillDefinition


def _meta_get(meta: dict[str, str], *names: str, default: str | None = None) -> str | None:
    """按多个候选 key 读取 metadata，兼容中划线和下划线命名。"""
    for name in names:
        if name in meta:
            return meta[name]
    return default


def _parse_bool(value: str | bool | None, default: bool = False) -> bool:
    """把 frontmatter 中的布尔配置解析成 Python bool。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "off", ""}


def _parse_tool_list(value: str | None) -> list[str] | None:
    """解析 allowed/disallowed tools，支持 JSON 数组和逗号分隔字符串。"""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
        raw = raw.strip("[]")
    items = [item.strip().strip('"').strip("'") for item in raw.split(",")]
    return [item for item in items if item]


def _read_frontmatter_meta(file_path: Path) -> dict[str, str]:
    """只读取 `SKILL.md` 的 frontmatter，避免 discovery 阶段加载正文。"""
    with file_path.open(encoding="utf-8") as f:
        first = f.readline()
        if first.strip() != "---":
            return {}

        lines = [first.rstrip("\n")]
        for line in f:
            lines.append(line.rstrip("\n"))
            if line.strip() == "---":
                return parse_frontmatter("\n".join(lines)).meta

    return {}


class SkillRegistry:
    """发现并缓存当前会话可用的 skills。"""

    def __init__(self, user_dir: Path | None = None, project_dir: Path | None = None):
        """初始化 registry，可传入目录便于测试或自定义运行环境。"""
        self.user_dir = user_dir
        self.project_dir = project_dir
        self._skills: list[SkillDefinition] | None = None
        self.errors: list[str] = []

    def discover(self) -> list[SkillDefinition]:
        """发现并返回可用 skills；项目级同名 skill 覆盖用户级。"""
        if self._skills is not None:
            return self._skills

        self.errors = []
        skills: dict[str, SkillDefinition] = {}
        self._load_skills_from_dir(self._user_dir(), "user", skills)
        self._load_skills_from_dir(self._project_dir(), "project", skills)
        self._skills = list(skills.values())
        return self._skills

    def get(self, name: str) -> SkillDefinition | None:
        """按名称查找已发现的 skill。"""
        for skill in self.discover():
            if skill.name == name:
                return skill
        return None

    def reset(self) -> None:
        """清空 discovery 缓存，让下一次调用重新扫描目录。"""
        self._skills = None
        self.errors = []

    def _user_dir(self) -> Path:
        """返回默认或自定义的用户级 skill 目录。"""
        return self.user_dir or (Path.home() / ".claude" / "skills")

    def _project_dir(self) -> Path:
        """返回默认或自定义的项目级 skill 目录。"""
        return self.project_dir or (Path.cwd() / ".claude" / "skills")

    def _load_skills_from_dir(
        self, base_dir: Path, source: str, skills: dict[str, SkillDefinition]
    ) -> None:
        """从一个 skill 根目录加载所有包含 `SKILL.md` 的子目录。"""
        if not base_dir.is_dir():
            return
        for entry in sorted(base_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            if not skill_file.exists():
                continue
            skill = self._parse_skill_file(skill_file, source, str(entry))
            if skill:
                skills[skill.name] = skill

    def _parse_skill_file(
        self, file_path: Path, source: str, skill_dir: str
    ) -> SkillDefinition | None:
        """把一个 `SKILL.md` 的 metadata 转成 `SkillDefinition`。"""
        try:
            meta = _read_frontmatter_meta(file_path)

            name = (_meta_get(meta, "name") or file_path.parent.name or "unknown").strip()
            context = (_meta_get(meta, "context", default="inline") or "inline").strip()
            if context not in {"inline", "fork"}:
                context = "inline"

            return SkillDefinition(
                name=name,
                description=_meta_get(meta, "description", default="") or "",
                when_to_use=_meta_get(meta, "when_to_use", "when-to-use"),
                allowed_tools=_parse_tool_list(_meta_get(meta, "allowed_tools", "allowed-tools")),
                disallowed_tools=_parse_tool_list(
                    _meta_get(meta, "disallowed_tools", "disallowed-tools")
                ),
                user_invocable=_parse_bool(
                    _meta_get(meta, "user_invocable", "user-invocable"), default=True
                ),
                disable_model_invocation=_parse_bool(
                    _meta_get(meta, "disable_model_invocation", "disable-model-invocation"),
                    default=False,
                ),
                context=context,
                agent=_meta_get(meta, "agent"),
                argument_hint=_meta_get(meta, "argument_hint", "argument-hint"),
                prompt_template="",
                source=source,
                skill_dir=skill_dir,
                path=str(file_path),
            )
        except Exception as exc:
            self.errors.append(f"{file_path}: {exc}")
            return None


_default_registry = SkillRegistry()


def get_default_registry() -> SkillRegistry:
    """返回进程内默认 skill registry。"""
    return _default_registry


def discover_skills() -> list[SkillDefinition]:
    """使用默认 registry 发现当前项目可用的 skills。"""
    return get_default_registry().discover()


def get_skill_by_name(name: str) -> SkillDefinition | None:
    """使用默认 registry 按名称查找 skill。"""
    return get_default_registry().get(name)


def reset_skill_cache() -> None:
    """清空默认 registry 的 discovery 缓存。"""
    get_default_registry().reset()
