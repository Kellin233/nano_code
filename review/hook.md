# Hook Code Review

Scope: `nanocode/nanocode/hooks` and the direct hook call sites in agent/tool runtime.

## Findings

### High: normal command hooks can silently fail open

Location: `nanocode/nanocode/hooks/runner.py`

`run_command_hook()` wraps `process.communicate()` in `asyncio.wait_for()`. In the current environment, a simple hook process can finish with return code `0` while `communicate()` still times out. The timeout handler then calls `process.kill()`, which can raise `ProcessLookupError` because the process has already exited. That exception is caught by the outer `except` and converted to `allow` when `fail_closed` is false.

Impact: `deny`, `modify`, and `append_context` hook outputs can be ignored with no visible failure. Existing hook tests show this: the deny hook does not block execution, the modify hook does not change the tool input, and PostToolUse context is not appended.

Suggested fix: avoid canceling `communicate()` in a way that loses already-produced output. One option is to write stdin, close it, then read stdout/stderr and wait under a single timeout task, handling already-exited processes before kill. Also preserve timeout/errors in `HookOutput.error`.

### High: PreToolUse modify bypasses tool validation

Location: `nanocode/nanocode/tools/runtime.py`

The runtime validates the original model input before hooks run, then applies `hook_result.updated_input` directly:

```python
validation = await tool.validate(call.input, ctx)
...
if hook_result.action == "modify" and hook_result.updated_input is not None:
    inp = hook_result.updated_input
```

There is no second `tool.validate(inp, ctx)` after hook modification.

Impact: a hook can replace input with a non-object, omit required fields, or provide invalid values and bypass the normal validation path. This can lead to permission checks or tool calls receiving malformed input.

Suggested fix: after every `modify`, validate the new input before continuing. If validation fails, return an error tool result and do not execute the tool.

### Medium: bad timeout_ms can crash hook loading

Location: `nanocode/nanocode/hooks/config.py`

`_load_hooks()` parses `timeout_ms` using `int(item.get("timeout_ms") or 3000)` outside a guarded conversion. A setting such as `"timeout_ms": "bad"` raises `ValueError` and can break `HookManager.capture()`.

Impact: one malformed hook item can prevent all hook capture from completing.

Suggested fix: validate `timeout_ms` per item, skip invalid entries or fall back to the default, and preferably reject non-positive values.

### Medium: hook stdout JSON is not structurally validated

Location: `nanocode/nanocode/hooks/runner.py`

After `json.loads(text)`, the code assumes the result is a dict and accepts arbitrary `action` values:

```python
return HookOutput(
    action=parsed.get("action", "allow"),
    ...
)
```

If the hook outputs a list, string, or `{"action": "block"}`, behavior becomes either fail-open via exception or a value the callers silently ignore.

Impact: malformed hook output is not reported clearly and can fail open unexpectedly.

Suggested fix: require parsed output to be a dict and require action to be one of `allow`, `deny`, `modify`, or `append_context`. Return a clear error, honoring `fail_closed`.

### Medium: chained hooks do not see earlier modifications

Location: `nanocode/nanocode/hooks/config.py` and `nanocode/nanocode/tools/runtime.py`

`HookManager.run()` executes all matching hooks with the same original `hook_input`. If the first PreToolUse hook returns `modify`, later hooks still receive the original input, not the updated one.

Impact: multiple hooks cannot be composed reliably. A policy hook after a rewrite hook may inspect stale input and allow or deny based on the wrong command.

Suggested fix: either make `HookManager.run()` update the in-flight `HookInput` after `modify`, or move sequential hook application into the runtime so each hook sees the latest input.

## Verification Notes

Commands attempted from `/root/EvoCode/nanocode`:

```bash
python -m pytest nanocode/test/test_hooks_runtime.py nanocode/test/v1/test_permissions_hooks_sandbox_v1.py nanocode/test/v1/test_tool_runtime_v1.py
```

Result: could not run because the current Python environment does not have `pytest` installed.

```bash
python -m unittest discover -s test -p 'test_hooks_runtime.py'
```

Result: failed two tests. `test_pre_tool_hook_can_deny_tool_execution` did not block execution, and `test_pre_tool_hook_can_modify_tool_input` still executed the original input.

```bash
python -m unittest discover -s test/v1 -p 'test_tool_runtime_v1.py'
```

Result: failed `test_post_hook_context_survives_large_result_persistence`; `extra_messages` was empty instead of containing the PostToolUse context.

```bash
python -m unittest discover -s test/v1 -p 'test_permissions_hooks_sandbox_v1.py'
```

Result: failed during import with a circular import involving `nanocode.permissions.policy` and `nanocode.tools.permissions`. This is outside the hooks directory, but it blocks this test file under unittest discovery.
