---
name: trace-analysis
description: Analyze AI interaction traces (JSONL) under temp/chat_log to judge whether agent tool calls behaved as expected and locate failure root causes. Use when the user asks to "review/analyze whether the trace is as expected" or to debug agent behavior.
---

# AI Interaction Trace Analysis

## When to use

- The user asks to "review / analyze the latest AI interaction trace" or "whether it is as expected"
- Debugging agent tool-call failures, wrong results, or detours
- Reconstructing agent behavior from real run records instead of guessing

## Trace location & structure

- Location: `temp/chat_log/trace_<session_id>.jsonl` (sort by mtime, take the newest: `ls -lat temp/chat_log/`)
- Format: one JSON event per line, distinguished by `event_type`

| event_type | Meaning | Key fields |
|---|---|---|
| `session_start` | Session start | source_dir / test_dir / project_file snapshot |
| `user_message` | User message | message |
| `llm_request` / `llm_response` | LLM round-trip | system_prompt / content / tool_calls |
| `agent_step` | ReAct step | step / actions |
| `tool_call` / `tool_result` | Tool call / result | tool_name / arguments / observation (linked via span_id) |
| `done` | Final answer | answer |
| `error` | Exception | source / message |

Note: `source_dir`/`test_dir` in `session_start` are often the initial empty values; the real directories arrive in later WebSocket params, or must be inferred from tool arguments.

## Analysis workflow

1. **Locate the newest trace**: `ls -lat temp/chat_log/ | head`, take the most recent `.jsonl`.
2. **Count events**: `Counter(event_type)`, sanity-check tool_call/tool_result/done/error counts.
3. **Extract tool outcomes**: join tool_call ↔ tool_result by `span_id`, print `tool_name` + `observation` summary for each.
4. **Judge success**: observation starting with `❌` / `Error` / `ERROR` is a failure, otherwise success.
5. **Read done answers**: confirm the agent actually answered the user's question correctly (not just "tools ran").
6. **Locate root cause**: for failures, go through the checklist below.

## Checklist (known pitfalls in this project)

1. **Tool failed with an empty error**: `❌ 工具 'bash' 执行失败: ` (nothing after the colon) = exception `str()` is empty. Root cause: `asyncio.create_subprocess_*` raises `NotImplementedError` under the Windows **SelectorEventLoop** (uvicorn's default). Fix: use `asyncio.to_thread + subprocess.run`. Reproduce with `WindowsSelectorEventLoopPolicy` — confirm `NotImplementedError` and `str(e) == ''`.

2. **Garbled bash output**: `'wc' �����ڲ����ⲿ���` = cmd.exe's GBK Chinese decoded as UTF-8. Gotcha: `locale.getpreferredencoding(False)` returns `utf-8` under `PYTHONUTF8=1` (set in `main.py`), which defeats a "UTF-8 → locale" fallback. Fix: hardcode the fallback to `gbk`, do not rely on locale.

3. **Path escapes workspace**: `Error: Path escapes workspace: ...` = safe_path's workspace boundary is too narrow; the directory the agent wants to read (e.g. UML project files) is outside `source_dir`/`test_dir`/`design_dir`. Check whether a resource directory is missing.

4. **Agent uses Linux commands that fail on Windows**: `'wc'/'xargs'/'ls'/'pwd' is not recognized as an internal or external command` = agent assumed a Linux environment. Check whether it eventually adapted (switched to `python -c` or PowerShell). Readable error messages speed up adaptation; garbled ones slow it down.

5. **Agent takes detours**: repeatedly switching commands for the same goal (wc → xargs → python → powershell). Usually caused by unreadable errors (garbled) or a prompt that doesn't steer the agent toward cross-platform approaches.

6. **bash bypasses safe_path**: bash is not constrained by safe_path (it can read/write any path), so a `read_file` blocked by safe_path gets worked around via bash. Seeing "read_file FAIL followed by bash reading the same file" is exactly this.

## "As expected" criteria

- Tool calls show **zero FAIL** (especially bash, which used to fail entirely)
- bash output has **no garbling** (Chinese error messages readable)
- `done` answers **correctly answer the user** (verify objective facts: counts, line numbers, etc.)
- The agent did not **obviously detour** (repeatedly switching tools/commands for the same goal)

## Script

```python
# Extract tool outcomes + done answers from the newest trace
import json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
path = 'temp/chat_log/trace_<newest>.jsonl'  # confirm with ls -lat
calls = {}
with open(path, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        evt = json.loads(line)
        et = evt.get('event_type')
        if et == 'tool_call':
            calls[evt.get('span_id')] = evt
        elif et == 'tool_result':
            sid = evt.get('span_id')
            call = calls.get(sid, {})
            tn = call.get('tool_name') or evt.get('tool_name')
            obs = evt.get('observation', '')
            ok = not (obs.startswith('❌') or obs.startswith('Error') or obs.startswith('ERROR'))
            print(f'{"OK " if ok else "FAIL"} {tn:16s} {obs[:100].replace(chr(10), " ")}')
        elif et == 'done':
            print('DONE:', evt.get('answer', '')[:300].replace(chr(10), ' '))
```

When digging into garbling / empty errors, use `repr(observation)` to see the raw string, or reproduce the tool call directly (construct the tool instance + the matching event-loop policy) to get the real traceback.
