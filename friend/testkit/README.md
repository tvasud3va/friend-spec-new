# friendkit: black-box test kit (API reference for test authors)

Tests drive the program under test **only** through the interface its spec defines: command-line
arguments, stdin lines, stdout lines, exit codes, TCP. They never import or inspect implementation
code, so one suite applies to every target (language) unchanged.

## A test module

Files are named `test_*.py` in the suite directory. Stdlib only, plus:

```python
from friendkit import test, check, times_out, TestFailure, DEFAULT_TIMEOUT

@test("C-5", "C-6")                       # spec rule IDs this test verifies (required, ≥1)
def login_once(ctx):
    """One-line description."""
    p = ctx.spawn(args=["--data-dir", str(ctx.path("a")), "--json"])
    p.expect(event="started", user=None)  # waits for a JSON stdout line with these field values
    p.send("login alice")
    p.expect(event="logged_in", user="alice")

@test("P-1", "C-12", roles=("alice", "bob"), interop=True)   # also run across different targets
def two_users(ctx):
    a = ctx.spawn("alice", args=[...])    # the target playing role "alice"
    b = ctx.spawn("bob", args=[...])
```

Helper functions and shared constants may live in non-`test_` modules in the same directory
(`import helpers` works).

## `ctx` (one per test, everything is cleaned up afterwards)

| call | returns | notes |
|---|---|---|
| `ctx.spawn(role="default", args=(), json_lines=True, env=None)` | `Proc` | launches the target playing `role` with `args`, from an empty temporary working directory |
| `ctx.service(name)` | `Service` (`.port`) | starts a hand-written service declared in `friend.toml [services.<name>]`, once per test |
| `ctx.proxy(upstream_port)` | `NetProxy` (`.port`, `.down()`, `.up()`) | point the program at `proxy.port`; `down()` cuts live connections and refuses new ones (simulated offline); `up()` restores |
| `ctx.peer()` | `Peer` (`.port`, `.accept(timeout)`) | a scripted TCP server you control frame by frame |
| `ctx.path(name)` | `Path` | per-test scratch path (not created) |

## `Proc` (a running program)

Every `expect*` call starts searching at the **cursor** and moves it past the match, so consecutive
expects assert order while tolerating unrelated lines in between.

> **Pitfall: independent streams.** Because the cursor only moves forward, `expect(A)` followed by
> `expect(B)` *asserts that B comes after A*. When the spec leaves the relative order of two kinds of
> output undefined (e.g. acknowledgements vs. new submissions), do not interleave expects across them.
> Instead, wait for the last item you need with `expect_where`, then check the full set with `all(...)`,
> asserting only the orders the spec defines (e.g. compare the ordered list of one kind with itself).

| call | meaning |
|---|---|
| `send(line)` / `send_raw(text)` / `close_stdin()` | write to stdin (`send` appends `\n`) |
| `expect(timeout=5, **fields)` | next JSON line whose fields equal `fields`; returns the parsed object |
| `expect_where(pred, timeout, desc)` | next JSON object (or line, if `json_lines=False`) where `pred(x)` is true |
| `expect_line(text_or_compiled_regex, timeout)` | next stdout line equal to text / fully matching regex |
| `assert_none(within, **fields)` | sleep `within` seconds, fail if any line after the cursor matches |
| `all(**fields)` | every JSON line so far (ignores the cursor) matching `fields` |
| `lines`, `records` | raw lines / parsed JSON (non-JSON lines become `{"__invalid_json__": line}`) |
| `exit_code(timeout)` | exit code, or `None` if still running after `timeout` (then killed) |
| `kill()` | SIGKILL: simulates a crash; restart by spawning again with the same arguments |
| `running` | bool |

## `PeerConn` (from `peer.accept()`)

Newline-delimited text with JSON helpers: `recv()` (next JSON line), `recv_line()`, `recv_match(**fields)`
(skip until a frame matches), `send(obj)`, `send_raw(bytes_or_str)` (for split/combined/junk frames),
`close()`. Raw socket: `.sock`.

## Rules for good tests

1. **Cite rules.** Every test names the rule IDs it checks. Aim for every MUST rule to be covered.
2. **Assert behaviour the spec defines, nothing more.** Don't assert on unspecified text, timing
   tighter than the spec states, or ordering the spec leaves open. When the spec is ambiguous,
   don't test that point; report it.
3. **Every test must fail against a program that does nothing.** The harness checks this
   (`--null-target`). Tests that only assert absence (`assert_none`, `times_out`) must first
   establish positive progress (e.g. expect a startup line).
4. **Deterministic.** Use expectations with timeouts, never bare `sleep` to wait for something to happen.
   Use the default timeout (5 s) unless the spec states a time bound.
5. **Isolated.** Each test creates its own processes, data directories (`ctx.path`), services and proxies.
6. **Interop.** Scenarios between multiple parties should use `roles=(...)` and `interop=True` so they
   also run with different targets in each role.
