"""Load Python extensions from .nanocode/extensions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from .api import ExtensionAPI


def load_extensions(
    api: ExtensionAPI,
    *,
    directory: Path | None = None,
) -> list[ModuleType]:
    root = directory or (Path.cwd() / ".nanocode" / "extensions")
    if not root.is_dir():
        return []

    modules: list[ModuleType] = []
    for path in sorted(root.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module = _load_module(path)
        register = getattr(module, "register", None)
        if callable(register):
            register(api)
        modules.append(module)
    return modules


def _load_module(path: Path) -> ModuleType:
    name = f"nanocode_user_extension_{path.stem}_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load extension: {path}")
    module = importlib.util.module_from_spec(spec)
    loader = spec.loader
    loader.exec_module(module)
    return module
