"""friendkit: a generic black-box test kit for programs driven over stdin/stdout and TCP.

Hand-written, part of the generator. Stdlib only. It knows nothing about any particular
application: tests describe *what* to send and expect, the kit handles processes, timeouts,
network fault injection and scripted peers. API reference for test authors: README.md.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

DEFAULT_TIMEOUT = float(os.environ.get("FRIEND_TEST_TIMEOUT", "5"))
# Ceiling on every wait. The runner lowers it for --null-target: a do-nothing program never answers, so a
# test against it can only time out, and waiting the full timeout would make vacuity checks take minutes.
TIMEOUT_CAP = float("inf")


def _cap(timeout: float) -> float:
    return min(timeout, TIMEOUT_CAP)
_MISSING = object()


class TestFailure(AssertionError):
    """A test's expectation was not met (as opposed to the test itself crashing)."""


def check(cond: Any, msg: str) -> None:
    if not cond:
        raise TestFailure(msg)


def times_out(fn: Callable[[], Any]) -> bool:
    """True if fn raised TestFailure (e.g. an expected-to-not-happen wait timed out)."""
    try:
        fn()
        return False
    except TestFailure:
        return True


def _matches(rec: Any, match: dict) -> bool:
    return isinstance(rec, dict) and all(rec.get(k, _MISSING) == v for k, v in match.items())


# ── processes ─────────────────────────────────────────────────────────────────

class Proc:
    """A running program. stdout is collected line by line; with json_lines=True each line is parsed.

    Expectation methods keep a cursor: each successful expect* moves past the matched line, so a
    sequence of expects asserts order while tolerating unrelated lines in between.
    """

    def __init__(self, argv: list[str], cwd: str | Path, label: str, json_lines: bool = True,
                 env: dict[str, str] | None = None) -> None:
        self.argv, self.label, self.json_lines = argv, label, json_lines
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env={**os.environ, **(env or {})},
                                     start_new_session=True)
        self.lines: list[str] = []
        self.records: list[Any] = []  # parsed JSON per line (None if not JSON) when json_lines
        self.stderr: list[str] = []
        self.cursor = 0
        self._eof = False
        self._cv = threading.Condition()
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read_out(self) -> None:
        buf = b""
        while chunk := self.proc.stdout.read1(65536):
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self._add(raw.decode("utf-8", "replace"))
        if buf:
            self._add(buf.decode("utf-8", "replace"))
        with self._cv:
            self._eof = True
            self._cv.notify_all()

    def _add(self, line: str) -> None:
        with self._cv:
            self.lines.append(line)
            if self.json_lines:
                try:
                    self.records.append(json.loads(line))
                except json.JSONDecodeError:
                    self.records.append({"__invalid_json__": line})
            self._cv.notify_all()

    def _read_err(self) -> None:
        for raw in self.proc.stderr:
            self.stderr.append(raw.decode("utf-8", "replace").rstrip("\n"))

    # input
    def send(self, line: str) -> None:
        """Write one line (a trailing \\n is added) to stdin."""
        self.send_raw(line + "\n")

    def send_raw(self, text: str | bytes) -> None:
        check(self.proc.poll() is None,
              f"{self.label}: process exited (code {self.proc.returncode}) before input {str(text)[:80]!r}; stderr: {self.stderr_tail()}")
        try:
            self.proc.stdin.write(text.encode() if isinstance(text, str) else text)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise TestFailure(f"{self.label}: cannot write to stdin: {e}")

    def close_stdin(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    # expectations
    def expect(self, timeout: float = DEFAULT_TIMEOUT, **match: Any) -> dict:
        """Wait for the next JSON record after the cursor whose fields equal `match`."""
        return self.expect_where(lambda r: _matches(r, match), timeout, f"record matching {match}")

    def expect_where(self, pred: Callable[[Any], bool], timeout: float = DEFAULT_TIMEOUT, desc: str = "record") -> Any:
        items = self.records if self.json_lines else self.lines
        deadline = time.monotonic() + _cap(timeout)
        with self._cv:
            while True:
                for i in range(self.cursor, len(items)):
                    try:
                        ok = pred(items[i])
                    except Exception:
                        ok = False
                    if ok:
                        self.cursor = i + 1
                        return items[i]
                left = deadline - time.monotonic()
                if left <= 0 or self._eof:
                    raise TestFailure(f"{self.label}: timed out after {timeout}s waiting for {desc}\n"
                                      f"  output after cursor (last 8): {items[self.cursor:][-8:]}\n"
                                      f"  exited: {self.proc.poll()}  stderr: {self.stderr_tail()}")
                self._cv.wait(min(left, 0.05))

    def expect_line(self, pattern: str | re.Pattern, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Wait for the next stdout line equal to `pattern` (str) or fully matching it (compiled regex)."""
        ok = (lambda l: pattern.fullmatch(l) is not None) if isinstance(pattern, re.Pattern) else (lambda l: l == pattern)
        deadline = time.monotonic() + _cap(timeout)
        with self._cv:
            while True:
                for i in range(self.cursor, len(self.lines)):
                    if ok(self.lines[i]):
                        self.cursor = i + 1
                        return self.lines[i]
                left = deadline - time.monotonic()
                if left <= 0 or self._eof:
                    raise TestFailure(f"{self.label}: timed out after {timeout}s waiting for line {pattern!r}\n"
                                      f"  lines after cursor (last 8): {self.lines[self.cursor:][-8:]}")
                self._cv.wait(min(left, 0.05))

    def assert_none(self, within: float, **match: Any) -> None:
        """Wait `within` seconds and fail if any record after the cursor matches `match`."""
        time.sleep(within)
        with self._cv:
            hits = [r for r in self.records[self.cursor:] if _matches(r, match)]
        check(not hits, f"{self.label}: unexpected output matching {match}: {hits[:3]}")

    def all(self, **match: Any) -> list[dict]:
        """Every record so far (ignoring the cursor) whose fields equal `match`."""
        with self._cv:
            return [r for r in self.records if _matches(r, match)]

    # lifecycle
    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def exit_code(self, timeout: float = DEFAULT_TIMEOUT) -> int | None:
        """Exit code, or None if still running after `timeout` (the process is then killed)."""
        try:
            return self.proc.wait(_cap(timeout))
        except subprocess.TimeoutExpired:
            self.kill()
            return None

    def kill(self) -> None:
        """SIGKILL, no chance to clean up (simulates a crash)."""
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, 9)
            except (ProcessLookupError, PermissionError):
                self.proc.kill()
        self.proc.wait()

    def stderr_tail(self, n: int = 5) -> str:
        return " | ".join(self.stderr[-n:])


# ── services (hand-written components declared in friend.toml [services.*]) ───────

class Service:
    def __init__(self, name: str, cfg: dict, root: Path) -> None:
        argv = [sys.executable if a == "python3" else a for a in cfg["launch"]]
        self.name = name
        self.proc = subprocess.Popen(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     start_new_session=True)
        ready = re.compile(cfg["ready"])
        deadline = time.monotonic() + DEFAULT_TIMEOUT
        self.port: int | None = None
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            if m := ready.search(line):
                self.port = int(m.group(1)) if m.groups() else None
                return
        raise TestFailure(f"service {name}: did not print a line matching {cfg['ready']!r}")

    def stop(self) -> None:
        self.proc.kill()
        self.proc.wait()


# ── network fault injection ───────────────────────────────────────────────────

class NetProxy:
    """TCP proxy in front of `upstream_port`. down() drops live connections and refuses new ones; up() restores."""

    def __init__(self, upstream_port: int) -> None:
        self.upstream = upstream_port
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()
        self._lock = threading.Lock()
        self._socks: list[socket.socket] = []
        self._listener: socket.socket | None = None
        self.up()

    def up(self) -> None:
        with self._lock:
            if self._listener:
                return
            ls = socket.socket()
            ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ls.bind(("127.0.0.1", self.port))
            ls.listen(16)
            self._listener = ls
        threading.Thread(target=self._accept, args=(ls,), daemon=True).start()

    def down(self) -> None:
        with self._lock:
            ls, self._listener = self._listener, None
            socks, self._socks = self._socks, []
        if ls:
            ls.close()
        for s in socks:
            _shut(s)

    def _accept(self, ls: socket.socket) -> None:
        while True:
            try:
                down_sock, _ = ls.accept()
            except OSError:
                return
            try:
                up_sock = socket.create_connection(("127.0.0.1", self.upstream))
            except OSError:
                down_sock.close()
                continue
            with self._lock:
                if self._listener is not ls:
                    down_sock.close()
                    up_sock.close()
                    return
                self._socks += [down_sock, up_sock]
            for a, b in ((down_sock, up_sock), (up_sock, down_sock)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    @staticmethod
    def _pipe(src: socket.socket, dst: socket.socket) -> None:
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except OSError:
            pass
        _shut(src)
        _shut(dst)


def _shut(s: socket.socket) -> None:
    try:
        s.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        s.close()
    except OSError:
        pass


# ── scripted peer: a TCP server the test controls frame by frame ─────────────

class PeerConn:
    """One accepted connection, speaking newline-delimited text (JSON helpers included)."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.buf = b""

    def recv_line(self, timeout: float = DEFAULT_TIMEOUT) -> str:
        deadline = time.monotonic() + _cap(timeout)
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TestFailure(f"peer: no line received within {timeout}s")
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                raise TestFailure(f"peer: connection error: {e}")
            if not chunk:
                raise TestFailure("peer: connection closed by the program under test")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode("utf-8", "replace")

    def recv(self, timeout: float = DEFAULT_TIMEOUT) -> Any:
        line = self.recv_line(timeout)
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            raise TestFailure(f"peer: received invalid JSON {line!r}")

    def recv_match(self, timeout: float = DEFAULT_TIMEOUT, **match: Any) -> dict:
        """Skip frames until one whose fields equal `match` arrives."""
        deadline = time.monotonic() + _cap(timeout)
        while True:
            f = self.recv(max(0.01, deadline - time.monotonic()))
            if _matches(f, match):
                return f

    def send(self, obj: Any) -> None:
        self.send_raw((json.dumps(obj, ensure_ascii=False) + "\n").encode())

    def send_raw(self, data: bytes | str) -> None:
        self.sock.sendall(data.encode() if isinstance(data, str) else data)

    def close(self) -> None:
        _shut(self.sock)


class Peer:
    """A listening socket on 127.0.0.1; the program under test connects to `port`."""

    def __init__(self) -> None:
        self.ls = socket.socket()
        self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(8)
        self.port = self.ls.getsockname()[1]

    def accept(self, timeout: float = DEFAULT_TIMEOUT) -> PeerConn:
        self.ls.settimeout(_cap(timeout))
        try:
            sock, _ = self.ls.accept()
        except socket.timeout:
            raise TestFailure(f"peer: nothing connected within {timeout}s")
        sock.settimeout(None)
        return PeerConn(sock)

    def close(self) -> None:
        self.ls.close()


# ── per-test context ──────────────────────────────────────────────────────────

class Ctx:
    """Everything a test needs; every resource is cleaned up when the test ends."""

    def __init__(self, root: Path, roles: dict[str, str], targets: dict[str, dict], services: dict[str, dict],
                 launch_override: list[str] | None = None) -> None:
        self.root, self.roles, self.targets, self.services_cfg = root, roles, targets, services
        self.launch_override = launch_override
        self.tmp = Path(tempfile.mkdtemp(prefix="friendkit-"))
        self._cleanup: list[Callable[[], None]] = []
        self._services: dict[str, Service] = {}

    def target_of(self, role: str) -> str:
        return self.roles.get(role, self.roles["default"])

    def spawn(self, role: str = "default", args: Iterable[str] = (), json_lines: bool = True,
              env: dict[str, str] | None = None) -> Proc:
        """Launch the target assigned to `role` with extra `args`, from a fresh empty working directory."""
        target = self.target_of(role)
        base = self.launch_override or self.targets[target]["launch_argv"]
        cwd = tempfile.mkdtemp(prefix="friendkit-cwd-", dir=self.tmp)
        p = Proc(list(base) + list(args), cwd, label=f"{role}({target})", json_lines=json_lines, env=env)
        self._cleanup.append(p.kill)
        return p

    def service(self, name: str) -> Service:
        """Start (once per test) a service declared in friend.toml [services.<name>]."""
        if name not in self._services:
            check(name in self.services_cfg, f"unknown service {name!r}; declared: {sorted(self.services_cfg)}")
            svc = Service(name, self.services_cfg[name], self.root)
            self._services[name] = svc
            self._cleanup.append(svc.stop)
        return self._services[name]

    def proxy(self, upstream_port: int) -> NetProxy:
        p = NetProxy(upstream_port)
        self._cleanup.append(p.down)
        return p

    def peer(self) -> Peer:
        p = Peer()
        self._cleanup.append(p.close)
        return p

    def path(self, name: str) -> Path:
        """A per-test scratch path (not created)."""
        return self.tmp / name

    def close(self) -> None:
        for fn in reversed(self._cleanup):
            try:
                fn()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ── registry ──────────────────────────────────────────────────────────────────

TESTS: list[dict] = []


def test(*rules: str, roles: tuple[str, ...] = (), interop: bool = False):
    """Register a test verifying spec `rules`.

    interop=True: the test is also run with each ordered pair of different targets assigned to
    `roles` (e.g. roles=("alice", "bob")), proving implementations work with each other.
    """
    def deco(fn):
        check(rules, f"{fn.__name__}: a test must name the spec rules it verifies")
        check(not interop or len(roles) >= 2, f"{fn.__name__}: interop tests must name at least two roles")
        TESTS.append({"name": fn.__name__, "fn": fn, "rules": list(rules), "roles": list(roles), "interop": interop,
                      "module": fn.__module__, "doc": (fn.__doc__ or "").strip().split("\n")[0]})
        return fn
    return deco


conformance = test
