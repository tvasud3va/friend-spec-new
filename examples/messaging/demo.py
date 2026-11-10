#!/usr/bin/env python3
"""Watch the assignment scenario run: Alice on one client, Bob on the other, through the real server.

Hand-written (not generated). Run from anywhere after `friend generate`:

    python3 examples/messaging/demo.py            # Alice on Python, Bob on TypeScript
    python3 examples/messaging/demo.py --swap     # Alice on TypeScript, Bob on Python

"Offline" is a real network cut: each client reaches the server through a small relay that the demo
switches off and on, so the clients see their connection drop and retry on their own (C-7).
"""
from __future__ import annotations

import argparse
import json
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIENTS = {"python": ["python3", str(HERE / "clients/python/main.py")],
           "typescript": ["node", str(HERE / "clients/typescript/dist/main.js")]}
TIMEOUT = 10.0


class Relay:
    """A TCP relay on a free port to the server. down() cuts every connection and refuses new ones."""

    def __init__(self, target: int) -> None:
        self.target, self.up_ = target, True
        self.conns: list[socket.socket] = []
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            c, _ = self.sock.accept()
            if not self.up_:
                c.close()
                continue
            s = socket.create_connection(("127.0.0.1", self.target))
            self.conns += [c, s]
            for a, b in ((c, s), (s, c)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket) -> None:
        try:
            while data := a.recv(65536):
                b.sendall(data)
        except OSError:
            pass
        for x in (a, b):
            try:
                x.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def down(self) -> None:
        self.up_ = False
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.conns.clear()

    def up(self) -> None:
        self.up_ = True


class Client:
    """One client process in --json mode; events are read on a thread and waited for by shape."""

    def __init__(self, name: str, lang: str, port: int, data: Path) -> None:
        self.name, self.lang, self.events = name, lang, queue.Queue()
        self.proc = subprocess.Popen(CLIENTS[lang] + ["--server", f"127.0.0.1:{port}", "--data-dir", str(data), "--json"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.events.put(json.loads(line))

    def cmd(self, line: str) -> None:
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def wait(self, event: str, **match) -> dict:
        end = time.monotonic() + TIMEOUT
        while (left := end - time.monotonic()) > 0:
            try:
                e = self.events.get(timeout=left)
            except queue.Empty:
                break
            if e.get("event") == event and all(e.get(k) == v for k, v in match.items()):
                return e
        sys.exit(f"\n✗ {self.name} ({self.lang}) never showed `{event}` {match or ''}: the demo failed")

    def ask(self, what: str) -> list[dict]:
        self.cmd(what)
        return self.wait(what)["messages"]


def say(text: str) -> None:
    print(text, flush=True)
    time.sleep(0.4)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--swap", action="store_true", help="Alice on TypeScript, Bob on Python")
    langs = ("typescript", "python") if ap.parse_args().swap else ("python", "typescript")
    missing = [l for l in langs if not Path(CLIENTS[l][1]).exists()]
    if missing:
        sys.exit(f"build the clients first (cd {HERE} && friend generate): missing {missing}")

    server = subprocess.Popen([sys.executable, str(HERE / "server/server.py"), "--port", "0"], stdout=subprocess.PIPE, text=True)
    port = int(server.stdout.readline().split()[1])
    tmp = Path(tempfile.mkdtemp(prefix="friend-demo-"))
    net_a, net_b = Relay(port), Relay(port)
    alice = Client("Alice", langs[0], net_a.port, tmp / "alice")
    bob = Client("Bob", langs[1], net_b.port, tmp / "bob")
    A, B = f"Alice ({alice.lang})", f"Bob ({bob.lang})"
    try:
        say(f"server on port {port} · {A} · {B}\n")
        for c in (alice, bob):
            c.wait("started")
            c.cmd(f"login {c.name}")
            c.wait("logged_in")
            c.wait("status", online=True)
        say(f"  {A} and {B} log in and come online\n")

        m1 = "Hi Bob, I have something important to tell you"
        say(f"1. {A} → Bob: {m1}")
        q = alice.cmd(f"send Bob {m1}") or alice.wait("queued", body=m1)
        alice.wait("sent", id=q["id"])
        bob.wait("message", id=q["id"])
        say(f"   ✓ Bob sees: [Alice] {m1}\n")

        m2 = "What is it?"
        say(f"2. {B} → Alice: {m2}")
        q = bob.cmd(f"send Alice {m2}") or bob.wait("queued", body=m2)
        alice.wait("message", id=q["id"])
        say(f"   ✓ Alice sees: [Bob] {m2}\n")

        m3 = "I'm moving to Mars"
        say(f"3. Alice goes offline and sends: {m3}")
        net_a.down()
        alice.wait("status", online=False)
        q3 = alice.cmd(f"send Bob {m3}") or alice.wait("queued", body=m3)
        say(f"   ✓ queued in Alice's outbox: {[m['body'] for m in alice.ask('outbox')]}\n")

        m4 = "Wait, I can't hear you"
        say(f"4. Bob goes offline and sends: {m4}")
        net_b.down()
        bob.wait("status", online=False)
        q4 = bob.cmd(f"send Alice {m4}") or bob.wait("queued", body=m4)
        say(f"   ✓ queued in Bob's outbox: {[m['body'] for m in bob.ask('outbox')]}\n")

        say("5. Alice reconnects")
        net_a.up()
        alice.wait("status", online=True)
        alice.wait("sent", id=q3["id"])
        say(f"   ✓ her queued message reached the server; outbox now {alice.ask('outbox')}\n")

        say("6. Alice goes offline again")
        net_a.down()
        alice.wait("status", online=False)
        say("   ✓ offline\n")

        say("7. Bob reconnects")
        net_b.up()
        bob.wait("status", online=True)
        bob.wait("message", id=q3["id"])
        bob.wait("sent", id=q4["id"])
        say(f"   ✓ Bob receives: [Alice] {m3}")
        say(f"   ✓ and his queued message is delivered to the server; Bob's inbox: {[m['body'] for m in bob.ask('inbox')]}\n")

        say("8. (bonus) Alice comes back")
        net_a.up()
        alice.wait("message", id=q4["id"])
        say(f"   ✓ Alice receives: [Bob] {m4}; Alice's inbox: {[m['body'] for m in alice.ask('inbox')]}\n")
        say("the scenario works across both clients")
        return 0
    finally:
        for p in (alice.proc, bob.proc, server):
            p.kill()


if __name__ == "__main__":
    sys.exit(main())
