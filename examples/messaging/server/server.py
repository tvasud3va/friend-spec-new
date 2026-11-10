#!/usr/bin/env python3
"""message-friend server: single process, in-memory, implements spec/core/protocol.md.

Hand-written (not under generator discipline). Rule IDs in comments point at the spec.
Run: python3 server/server.py [--host 127.0.0.1] [--port 7070]   (--port 0 picks a free port)
Prints "LISTENING <port>" on stdout once ready.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")  # P-7
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")  # P-9
PROTOCOL = 1


def valid_body(body: object) -> bool:  # P-8
    return isinstance(body, str) and 1 <= len(body) <= 4000 and not body.isspace()


@dataclass
class Message:
    id: str
    sender: str
    to: str
    body: str
    seq: int

    def frame(self) -> dict:
        return {"type": "deliver", "id": self.id, "from": self.sender, "to": self.to,
                "body": self.body, "seq": self.seq}


@dataclass
class Conn:
    writer: asyncio.StreamWriter
    user: str | None = None

    def send(self, frame: dict) -> None:
        if self.writer.is_closing():
            return
        self.writer.write((json.dumps(frame, ensure_ascii=False) + "\n").encode())


@dataclass
class Server:
    seq: int = 0
    accepted: dict[tuple[str, str], int] = field(default_factory=dict)  # (sender, id) -> seq, P-20
    pending: dict[str, list[Message]] = field(default_factory=dict)     # recipient -> ordered, P-23
    online: dict[str, Conn] = field(default_factory=dict)               # P-14

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = Conn(writer)
        try:
            while line := await reader.readline():  # P-2; P-22 sequential per connection
                self.on_line(conn, line)
                await writer.drain()
        except (ConnectionError, asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            if conn.user and self.online.get(conn.user) is conn:
                del self.online[conn.user]
            writer.close()

    def on_line(self, conn: Conn, line: bytes) -> None:
        try:
            frame = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            frame = None
        if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
            return conn.send(err("bad_frame", "frame must be a JSON object with a string 'type'"))  # P-6
        kind = frame["type"]
        if kind == "hello":
            return self.on_hello(conn, frame)
        if conn.user is None:
            return conn.send(err("not_identified", "send hello first"))  # P-11
        if kind == "send":
            return self.on_send(conn, frame)
        if kind == "delivered":
            return self.on_delivered(conn, frame)
        conn.send(err("unknown_type", kind))  # P-5

    def on_hello(self, conn: Conn, frame: dict) -> None:
        user = frame.get("user")
        if not isinstance(user, str) or not NAME_RE.match(user):
            conn.send(err("invalid_name", str(user)))  # P-12
            return conn.writer.close()
        if frame.get("protocol") != PROTOCOL:
            conn.send(err("unsupported_protocol", str(frame.get("protocol"))))
            return conn.writer.close()
        old = self.online.get(user)
        if old is not None and old is not conn:  # P-14
            old.send(err("replaced", "a newer connection logged in as this user"))
            old.writer.close()
        conn.user = user
        self.online[user] = conn
        conn.send({"type": "welcome", "user": user, "protocol": PROTOCOL})
        for msg in self.pending.get(user, []):  # P-13, ascending seq by construction
            conn.send(msg.frame())

    def on_send(self, conn: Conn, frame: dict) -> None:
        sender = conn.user  # P-15: ignore any client-supplied 'from'
        mid, to, body = frame.get("id"), frame.get("to"), frame.get("body")
        if not isinstance(mid, str) or not UUID4_RE.match(mid):
            return conn.send(err("bad_frame", "send.id must be a lowercase uuid4"))
        key = (sender, mid)
        if key in self.accepted:  # P-20 idempotent retry
            return conn.send({"type": "ack", "id": mid, "seq": self.accepted[key]})
        if not isinstance(to, str) or not NAME_RE.match(to) or to == sender:  # P-16, P-17
            return conn.send(err("invalid_recipient", str(to), mid))
        if not valid_body(body):
            return conn.send(err("invalid_body", "1-4000 code points, not all whitespace", mid))
        self.seq += 1  # P-10
        msg = Message(mid, sender, to, body, self.seq)
        self.accepted[key] = msg.seq
        self.pending.setdefault(to, []).append(msg)  # P-18, P-19
        conn.send({"type": "ack", "id": mid, "seq": msg.seq})
        if (rcpt := self.online.get(to)) is not None:  # P-21
            rcpt.send(msg.frame())

    def on_delivered(self, conn: Conn, frame: dict) -> None:
        queue = self.pending.get(conn.user, [])  # P-23, P-24
        self.pending[conn.user] = [m for m in queue if m.id != frame.get("id")]


def err(code: str, detail: str = "", mid: str | None = None) -> dict:
    frame = {"type": "error", "code": code, "detail": detail}
    if mid is not None:
        frame["id"] = mid
    return frame


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7070)
    args = ap.parse_args()
    state = Server()
    srv = await asyncio.start_server(state.handle, args.host, args.port, limit=1 << 20)
    port = srv.sockets[0].getsockname()[1]
    print(f"LISTENING {port}", flush=True)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
