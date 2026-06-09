from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nanocode.capabilities.skills import (
    ActiveSkillManager,
    SkillInvocation,
    SkillRegistry,
)


def write_skill(base: Path, name: str, text: str) -> None:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text)


class SkillRegistryTests(unittest.TestCase):
    def test_project_skill_overrides_user_skill_and_parses_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "home" / ".claude" / "skills"
            project_dir = root / "project" / ".claude" / "skills"

            write_skill(
                user_dir,
                "review",
                """---
name: review
description: user review
user-invocable: false
---
user body
""",
            )
            write_skill(
                project_dir,
                "review",
                """---
name: review
description: project review
when-to-use: when reviewing diffs
user_invocable: true
disable-model-invocation: true
allowed-tools: ["read_file", "grep_search"]
disallowed_tools: run_shell, write_file
context: fork
agent: explore
argument-hint: <path>
---
project body
""",
            )

            registry = SkillRegistry(user_dir=user_dir, project_dir=project_dir)
            skills = registry.discover()

            self.assertEqual(len(skills), 1)
            skill = skills[0]
            self.assertEqual(skill.source, "project")
            self.assertEqual(skill.description, "project review")
            self.assertEqual(skill.when_to_use, "when reviewing diffs")
            self.assertTrue(skill.user_invocable)
            self.assertTrue(skill.disable_model_invocation)
            self.assertEqual(skill.allowed_tools, ["read_file", "grep_search"])
            self.assertEqual(skill.disallowed_tools, ["run_shell", "write_file"])
            self.assertEqual(skill.context, "fork")
            self.assertEqual(skill.agent, "explore")
            self.assertEqual(skill.argument_hint, "<path>")

    def test_invalid_context_falls_back_to_inline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "project" / ".claude" / "skills"
            write_skill(
                project_dir,
                "bad",
                """---
name: bad
description: bad context
context: strange
---
body
""",
            )

            skill = SkillRegistry(project_dir=project_dir).discover()[0]
            self.assertEqual(skill.context, "inline")

    def test_discovery_does_not_cache_skill_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / ".claude" / "skills"
            write_skill(
                project_dir,
                "lazy",
                """---
name: lazy
description: lazy body
---
original body
""",
            )

            registry = SkillRegistry(project_dir=project_dir)
            skill = registry.discover()[0]

            self.assertEqual(skill.prompt_template, "")
            self.assertEqual(skill.path, str(project_dir / "lazy" / "SKILL.md"))

    def test_invocation_lazily_loads_latest_skill_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / ".claude" / "skills"
            write_skill(
                project_dir,
                "lazy",
                """---
name: lazy
description: lazy body
---
original body
""",
            )

            registry = SkillRegistry(project_dir=project_dir)
            registry.discover()
            skill_file = project_dir / "lazy" / "SKILL.md"
            skill_file.write_text(
                """---
name: lazy
description: lazy body
---
updated body $ARGUMENTS
"""
            )

            result = SkillInvocation(registry).invoke("lazy", "now", invoked_by="user")

            self.assertTrue(result.ok)
            self.assertIn("updated body now", result.rendered_prompt)
            self.assertNotIn("original body", result.rendered_prompt)


class SkillInvocationTests(unittest.TestCase):
    def test_argument_replacement_and_skill_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / ".claude" / "skills"
            write_skill(
                project_dir,
                "args",
                """---
name: args
description: arg test
---
All=$ARGUMENTS
Zero=$0
Second=$ARGUMENTS[1]
Dir=${CLAUDE_SKILL_DIR}
""",
            )

            registry = SkillRegistry(project_dir=project_dir)
            result = SkillInvocation(registry).invoke("args", "alpha beta", invoked_by="user")

            self.assertTrue(result.ok)
            self.assertIn("All=alpha beta", result.rendered_prompt)
            self.assertIn("Zero=alpha", result.rendered_prompt)
            self.assertIn("Second=beta", result.rendered_prompt)
            self.assertIn(str(project_dir / "args"), result.rendered_prompt)
            self.assertNotIn("ARGUMENTS:\nalpha beta", result.rendered_prompt)

    def test_unused_arguments_are_appended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / ".claude" / "skills"
            write_skill(
                project_dir,
                "plain",
                """---
name: plain
description: plain
---
Do the thing.
""",
            )

            registry = SkillRegistry(project_dir=project_dir)
            result = SkillInvocation(registry).invoke("plain", "extra context", invoked_by="user")

            self.assertTrue(result.ok)
            self.assertTrue(result.rendered_prompt.endswith("ARGUMENTS:\nextra context"))

    def test_invocation_controls_user_and_model_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / ".claude" / "skills"
            write_skill(
                project_dir,
                "hidden",
                """---
name: hidden
description: hidden
user-invocable: false
---
hidden body
""",
            )
            write_skill(
                project_dir,
                "manual",
                """---
name: manual
description: manual
disable_model_invocation: true
---
manual body
""",
            )

            invocation = SkillInvocation(SkillRegistry(project_dir=project_dir))

            user_result = invocation.invoke("hidden", invoked_by="user")
            model_result = invocation.invoke("manual", invoked_by="model")
            manual_user = invocation.invoke("manual", invoked_by="user")

            self.assertFalse(user_result.ok)
            self.assertIn("not user-invocable", user_result.error or "")
            self.assertFalse(model_result.ok)
            self.assertIn("cannot be invoked by the model", model_result.error or "")
            self.assertTrue(manual_user.ok)


class ActiveSkillManagerTests(unittest.TestCase):
    def test_active_skill_context_respects_recent_order_and_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / ".claude" / "skills"
            write_skill(
                project_dir,
                "one",
                """---
name: one
description: one
---
one body
""",
            )
            write_skill(
                project_dir,
                "two",
                """---
name: two
description: two
---
two body
""",
            )
            invocation = SkillInvocation(SkillRegistry(project_dir=project_dir))
            manager = ActiveSkillManager(max_active=2, per_skill_token_budget=3, total_token_budget=100)

            manager.record(invocation.invoke("one", "first", invoked_by="user"))
            manager.record(invocation.invoke("two", "second", invoked_by="model"))
            context = manager.build_context()

            self.assertIn("[Active skill: two]", context)
            self.assertIn("[Active skill: one]", context)
            self.assertLess(context.find("[Active skill: two]"), context.find("[Active skill: one]"))
            self.assertIn("active skill truncated", context)


class DefaultRegistryPathTests(unittest.TestCase):
    def test_default_registry_uses_home_and_cwd(self) -> None:
        from nanocode.capabilities.skills import discover_skills, reset_skill_cache

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            project = root / "project"
            write_skill(
                project / ".claude" / "skills",
                "local",
                """---
name: local
description: local
---
body
""",
            )

            old_cwd = Path.cwd()
            try:
                os.chdir(project)
                with patch("pathlib.Path.home", return_value=home):
                    reset_skill_cache()
                    skills = discover_skills()
            finally:
                os.chdir(old_cwd)
                reset_skill_cache()

            self.assertEqual([s.name for s in skills], ["local"])

    def test_build_skill_descriptions_lists_each_skill_once_with_invocation_modes(self) -> None:
        from nanocode.capabilities.skills import build_skill_descriptions, reset_skill_cache

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            project = root / "project"
            write_skill(
                project / ".claude" / "skills",
                "both",
                """---
name: both
description: both paths
argument-hint: <topic>
---
body
""",
            )
            write_skill(
                project / ".claude" / "skills",
                "manual",
                """---
name: manual
description: user only
disable-model-invocation: true
---
body
""",
            )

            old_cwd = Path.cwd()
            try:
                os.chdir(project)
                with patch("pathlib.Path.home", return_value=home):
                    reset_skill_cache()
                    text = build_skill_descriptions()
            finally:
                os.chdir(old_cwd)
                reset_skill_cache()

            self.assertEqual(text.count("**both**"), 1)
            self.assertIn("invoke: user=/both <topic>, model=skill tool", text)
            self.assertIn("**manual**", text)
            self.assertIn("invoke: user=/manual", text)
            self.assertNotIn("Model-invocable skills", text)
            self.assertNotIn("User-invocable skills", text)


if __name__ == "__main__":
    unittest.main()
