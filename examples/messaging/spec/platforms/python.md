# Platform: Python client (`python`)

Platform specs choose *how*, never *what*. Behaviour is defined by `core/` and `extensions/`;
if anything here seems to contradict them, core wins and the conflict is a spec bug.

- **PY-1** (MUST) Python 3.11+ standard library only. No third-party packages, no `pip install`.
- **PY-2** (MUST) Entry point is `clients/python/main.py`, launched as `python3 clients/python/main.py [options]` from the repository root. It must also work when launched from any other working directory.
- **PY-3** (MUST) Networking and timers use `asyncio` (`asyncio.open_connection`). Stdin is read on a background thread that hands lines to the event loop (portable; avoids platform-specific pipe readers). Read bytes from `sys.stdin.buffer` and split on `b"\n"` yourself, decoding each line with `line.decode("utf-8", errors="replace")` (not `"utf-8-sig"`, so a leading U+FEFF is kept): text-mode `sys.stdin` uses universal newlines and would split on a lone `\r` (I-3).
- **PY-4** (MUST) Durable state is a SQLite database `state.sqlite3` in the data directory, via the `sqlite3` module. Each state change is one committed transaction (C-2).
- **PY-5** (MUST) Default data directory: `~/.message-friend/python`.
- **PY-6** (MUST) Message IDs come from `uuid.uuid4()` formatted with `str()` (already lowercase).
- **PY-7** (MUST) Body length (P-8) is `len(body)` on a `str` (Python strings count code points). The whitespace test (P-8) uses an explicit set of the code points listed in P-8, **not** `str.isspace()` or `str.strip()` (which treat U+001C–U+001F as whitespace).
- **PY-8** (MUST) Output uses `print(..., flush=True)` or an equivalent that flushes per line; JSON is serialised with `json.dumps(obj, ensure_ascii=False)`.
- **PY-9** (SHOULD) Code is split into small modules by concern (protocol/framing, storage, client state machine, CLI), each with type hints.
