import io
import unittest

from rich.console import Console

from nanocode.tui.renderer import TuiRenderer


def renderer_with_output(max_result_chars=80):
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, no_color=True, highlight=False, width=120)
    return TuiRenderer(console=console, max_result_chars=max_result_chars), output


def terminal_renderer_with_output(width=80):
    output = io.StringIO()
    console = Console(file=output, force_terminal=True, no_color=True, highlight=False, width=width)
    return TuiRenderer(console=console), output


def visible_terminal_text(stream: str) -> str:
    lines = [""]
    row = 0
    col = 0
    saved = (0, 0)
    index = 0
    while index < len(stream):
        if stream.startswith("\x1b[s", index):
            saved = (row, col)
            index += 3
            continue
        if stream.startswith("\x1b[u", index):
            row, col = saved
            index += 3
            continue
        if stream.startswith("\x1b[J", index):
            lines[row] = lines[row][:col]
            del lines[row + 1 :]
            index += 3
            continue

        char = stream[index]
        if char == "\n":
            row += 1
            col = 0
            while len(lines) <= row:
                lines.append("")
        else:
            line = lines[row]
            if col > len(line):
                line += " " * (col - len(line))
            lines[row] = line[:col] + char + line[col + 1 :]
            col += 1
        index += 1
    return "\n".join(lines)


class TuiRendererTest(unittest.TestCase):
    def test_welcome_renders_header_panel(self):
        class Agent:
            model = "gpt-test"

        class State:
            agent = Agent()

        renderer, output = renderer_with_output()

        renderer.welcome(State())

        text = output.getvalue()
        self.assertIn("Nano Code", text)
        self.assertIn("gpt-test", text)

    def test_tool_call_prints_compact_summary(self):
        renderer, output = renderer_with_output()

        renderer.tool_call("run_shell", {"command": "python -m unittest discover -s test -v"})

        text = output.getvalue()
        self.assertIn("running run_shell", text)
        self.assertIn("python -m unittest discover", text)

    def test_user_and_assistant_boundaries_are_visible(self):
        renderer, output = renderer_with_output()

        renderer.user_message("hello")
        renderer.assistant_delta("\nhi")

        text = output.getvalue()
        self.assertIn("hello", text)
        self.assertIn("• hi", text)

    def test_long_tool_result_is_truncated(self):
        renderer, output = renderer_with_output(max_result_chars=40)

        renderer.tool_result("read_file", "x" * 120)

        self.assertIn("120 chars total", output.getvalue())

    def test_file_change_result_uses_diff_lines(self):
        renderer, output = renderer_with_output()

        renderer.tool_result("edit_file", "Updated file\n@@ section\n- old\n+ new\nunchanged")

        text = output.getvalue()
        self.assertIn("Updated file", text)
        self.assertIn("@@ section", text)
        self.assertIn("- old", text)
        self.assertIn("+ new", text)

    def test_shell_failure_is_rendered_as_error(self):
        renderer, output = renderer_with_output()

        renderer.tool_result("run_shell", "Command failed with exit code 1")

        self.assertIn("error", output.getvalue())

    def test_live_footer_redraws_around_streaming_output(self):
        renderer, output = terminal_renderer_with_output()

        self.assertTrue(
            renderer.begin_live_footer(
                status="Working",
                detail="0s • esc to interrupt",
                model="gpt-5.5 high",
                cwd="~/EvoCode",
            )
        )
        renderer.assistant_delta("\nhello")
        renderer.assistant_delta(" world")
        renderer.end_live_footer()

        text = output.getvalue()
        visible = visible_terminal_text(text)
        self.assertIn("Working", text)
        self.assertIn("hello world", visible)
        self.assertIn("\x1b[J", text)
        self.assertIn("\x1b[s", text)
        self.assertIn("\x1b[u", text)

    def test_cost_is_separated_from_active_assistant_text(self):
        renderer, output = renderer_with_output()

        renderer.assistant_delta("\nhello")
        renderer.cost(10, 2)

        self.assertIn("hello\n\nTokens:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
