# Platform: Python (`python`)

Platform specs choose *how*, never *what*. If anything here seems to contradict `core/`, core wins.
Rule prefix: `PY`.

- **PY-1** (MUST) Python 3.11+ standard library only. No third-party packages, no `pip install`.
- **PY-2** (MUST) Entry point `{generated}/python/main.py`, launched as `python3 {generated}/python/main.py [args]` from the project root. It must also work when launched from any other working directory.
- **PY-3** (MUST) Every stdout line is flushed immediately (`print(..., flush=True)`); JSON output uses `json.dumps(obj, ensure_ascii=False)`.
- **PY-4** (MUST) If the program reads lines from stdin, it reads bytes from `sys.stdin.buffer` and splits on `b"\n"` itself (text-mode stdin also splits on a lone `\r`).
- **PY-5** (MUST) Text lengths in the spec are Unicode code points: `len(s)` on a `str`.
- **PY-6** (SHOULD) Small modules by concern, with type hints.
