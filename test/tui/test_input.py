import asyncio
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch

from nanocode.tui.input import ComposerLayout, TuiInput


class Feeder:
    def __init__(self, values):
        self.values = list(values)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.values:
            raise EOFError
        return self.values.pop(0)


class TuiInputTest(unittest.TestCase):
    def test_simple_read_returns_single_line(self):
        feeder = Feeder(["hello"])
        input_ = TuiInput(force_simple=True, input_fn=feeder)

        result = asyncio.run(input_.read("> "))

        self.assertEqual(result, "hello")
        self.assertEqual(feeder.prompts, ["> "])

    def test_simple_read_returns_none_on_eof(self):
        input_ = TuiInput(force_simple=True, input_fn=Feeder([]))

        result = asyncio.run(input_.read("> "))

        self.assertIsNone(result)

    def test_block_fallback_reads_until_matching_closer(self):
        input_ = TuiInput(force_simple=True, input_fn=Feeder(["{python", "a = 1", "print(a)", "python}"]))

        result = asyncio.run(input_.read("> "))

        self.assertEqual(result, "a = 1\nprint(a)")

    def test_completions_include_commands_and_skills(self):
        input_ = TuiInput(force_simple=True, input_fn=Feeder([]))

        input_.set_completions(["help", "exit"], ["commit"], {"/help": "Show commands"})

        self.assertEqual(input_._completer.words, ["/commit", "/exit", "/help"])
        self.assertEqual(input_._completer.meta["/help"], "Show commands")

    def test_status_text_formats_bottom_bar(self):
        input_ = TuiInput(force_simple=True, input_fn=Feeder([]))

        input_.set_status(model="gpt-5.5 high", cwd="~/EvoCode")

        self.assertIn("gpt-5.5 high", str(input_._bottom_bar()))
        self.assertIn("~/EvoCode", str(input_._bottom_bar()))

    def test_completer_supports_prompt_toolkit_async_api(self):
        class Document:
            text_before_cursor = "/he"

        input_ = TuiInput(force_simple=True, input_fn=Feeder([]))
        input_.set_completions(["help"], [], {"/help": "Show commands"})

        async def collect():
            return [item async for item in input_._completer.get_completions_async(Document(), None)]

        completions = asyncio.run(collect())

        self.assertIsInstance(completions, list)
        if completions:
            self.assertEqual(completions[0].text, "/help")

    def test_open_editor_returns_none_without_editor(self):
        input_ = TuiInput(force_simple=True, input_fn=Feeder([]))

        with patch.dict(os.environ, {"EDITOR": "", "VISUAL": ""}, clear=False):
            result = input_.open_editor("draft")

        self.assertIsNone(result)

    def test_composer_height_grows_with_wrapped_text(self):
        layout = ComposerLayout(max_height=8)

        self.assertEqual(layout.desired_height("", 40), 1)
        self.assertEqual(layout.desired_height("x" * 20, 12), 2)

    def test_composer_height_counts_explicit_newlines(self):
        layout = ComposerLayout(max_height=8)

        self.assertEqual(layout.desired_height("one\ntwo\n", 80), 3)

    def test_composer_height_handles_wide_characters(self):
        layout = ComposerLayout(max_height=8)

        self.assertEqual(layout.desired_height("你好你好你好", 8), 2)

    def test_composer_height_is_capped(self):
        layout = ComposerLayout(max_height=3)

        self.assertEqual(layout.desired_height("\n".join(["line"] * 20), 80), 3)

    def test_prompt_toolkit_window_uses_dynamic_height_when_available(self):
        input_ = TuiInput(force_simple=True, input_fn=Feeder([]))
        input_._terminal_width = lambda: 12
        with (
            contextlib.redirect_stderr(io.StringIO()),
            patch.object(sys.stdin, "isatty", return_value=True),
            patch.dict(os.environ, {"CI": ""}),
        ):
            input_._init_prompt_toolkit()
        if not input_.fancy:
            self.skipTest("prompt_toolkit is not available in this environment")

        with contextlib.redirect_stderr(io.StringIO()):
            app = input_._build_application("› ")
        input_window = app.layout.container.children[0]
        height = input_window.height()

        self.assertEqual(height.min, 1)
        self.assertEqual(height.preferred, 1)
        self.assertEqual(height.max, 1)

        input_window.content.buffer.text = "x" * 20
        height = input_window.height()

        self.assertEqual(height.min, 2)
        self.assertEqual(height.preferred, 2)
        self.assertEqual(height.max, 2)


if __name__ == "__main__":
    unittest.main()
