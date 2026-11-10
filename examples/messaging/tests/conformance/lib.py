"""Messaging-specific helpers for the hand-written (held-out) conformance suite, on top of the generic friendkit.

Hand-written. Knows the messaging control interface (--server/--data-dir/--json, `event` records);
the generic process/network machinery lives in friend/testkit/friendkit.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import friendkit
from friendkit import DEFAULT_TIMEOUT, TestFailure, check  # noqa: F401  (re-exported for tests)


class Client:
    """A messaging client process driven through the control interface."""

    def __init__(self, kctx: friendkit.Ctx, role: str, port: int, data_dir: Path, human: bool,
                 extra_args: list[str] | None, raw_args: list[str] | None) -> None:
        if raw_args is not None:
            args = raw_args
        else:
            args = ["--server", f"127.0.0.1:{port}", "--data-dir", str(data_dir)] + ([] if human else ["--json"]) + (extra_args or [])
        self.p = kctx.spawn(role, args, json_lines=not human)
        self.proc = self.p.proc
        self.label = self.p.label

    @property
    def events(self) -> list[Any]:
        return self.p.records

    def cmd(self, line: str) -> None:
        self.p.send(line)

    def wait(self, event: str, timeout: float = DEFAULT_TIMEOUT, **match: Any) -> dict:
        return self.p.expect(timeout, event=event, **match)

    def wait_line(self, text: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        return self.p.expect_line(text, timeout)

    def query(self, what: str) -> list[dict]:
        self.cmd(what)
        return self.wait(what)["messages"]

    def assert_no(self, event: str, within: float, **match: Any) -> None:
        self.p.assert_none(within, event=event, **match)

    def all(self, event: str) -> list[dict]:
        return self.p.all(event=event)

    def quit(self, timeout: float = DEFAULT_TIMEOUT) -> int:
        if self.p.running:
            try:
                self.cmd("quit")
            except TestFailure:
                pass
        code = self.p.exit_code(timeout)
        check(code is not None, f"{self.label}: did not exit within {timeout}s after quit")
        return code

    def exit_code(self, timeout: float = DEFAULT_TIMEOUT) -> int | None:
        return self.p.exit_code(timeout)

    def kill(self) -> None:
        self.p.kill()


class FakeConn:
    def __init__(self, conn: friendkit.PeerConn) -> None:
        self.c = conn

    def recv(self, timeout: float = DEFAULT_TIMEOUT) -> dict:
        return self.c.recv(timeout)

    def recv_type(self, kind: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
        return self.c.recv_match(timeout, type=kind)

    def send(self, frame: dict) -> None:
        self.c.send(frame)

    def raw(self, data: bytes) -> None:
        self.c.send_raw(data)

    def welcome(self, user: str) -> dict:
        hello = self.recv()
        check(hello.get("type") == "hello", f"first frame must be hello (C-8), got {hello}")
        self.send({"type": "welcome", "user": user, "protocol": 1})
        return hello

    def close(self) -> None:
        self.c.close()


class FakeServer:
    def __init__(self, peer: friendkit.Peer) -> None:
        self.peer, self.port = peer, peer.port

    def accept(self, timeout: float = DEFAULT_TIMEOUT) -> FakeConn:
        return FakeConn(self.peer.accept(timeout))


class Ctx:
    """Messaging view of a friendkit context."""

    def __init__(self, kctx: friendkit.Ctx) -> None:
        self.k = kctx
        self.tmp = kctx.tmp

    def server(self):
        return self.k.service("server")

    def proxy(self) -> friendkit.NetProxy:
        return self.k.proxy(self.server().port)

    def fake(self) -> FakeServer:
        return FakeServer(self.k.peer())

    def client(self, role: str, port: int, human: bool = False, extra_args: list[str] | None = None,
               raw_args: list[str] | None = None) -> Client:
        return Client(self.k, role, port, self.tmp / role, human, extra_args, raw_args)


def conformance(*rules: str, interop: bool = False):
    """Register a messaging test; the test function receives a messaging Ctx."""
    def deco(fn):
        def run(kctx: friendkit.Ctx) -> None:
            fn(Ctx(kctx))
        run.__name__, run.__doc__, run.__module__ = fn.__name__, fn.__doc__, fn.__module__
        friendkit.test(*rules, roles=("alice", "bob"), interop=interop)(run)
        return fn
    return deco
