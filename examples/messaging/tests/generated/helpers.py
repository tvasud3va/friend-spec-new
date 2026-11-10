"""Shared helpers for the generated conformance suite (black-box only)."""
import json
import re
import socket
import time
import uuid

from friendkit import TestFailure

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

WELCOME_TEXT = "Welcome to message-friend. Log in with: login <your-name>"


def ensure(cond, msg):
    if not cond:
        raise TestFailure(msg)


def new_id():
    return str(uuid.uuid4())


def send_bytes(p, data):
    """Write raw (possibly ill-formed UTF-8) bytes to the program's stdin."""
    try:
        p.send_raw(data)
    except TypeError:
        p.send_raw(data.decode("utf-8", "surrogateescape"))


def client_args(port=None, data=None, json_mode=True, extra=()):
    args = []
    if port is not None:
        args += ["--server", f"127.0.0.1:{port}"]
    if data is not None:
        args += ["--data-dir", str(data)]
    if json_mode:
        args.append("--json")
    return args + list(extra)


def spawn(ctx, role, port, data, json_mode=True):
    return ctx.spawn(role, args=client_args(port, data, json_mode), json_lines=json_mode)


def fresh(ctx, role, port, data, name):
    """Start a client on an empty data dir and log in as `name`."""
    p = spawn(ctx, role, port, data)
    p.expect(event="started", user=None)
    p.send(f"login {name}")
    p.expect(event="logged_in", user=name)
    return p


def online(ctx, role, port, data, name):
    """Start a client, log in, and wait until it is ONLINE."""
    p = fresh(ctx, role, port, data, name)
    p.expect(event="status", online=True)
    return p


def restart(ctx, role, port, data, name):
    """Start a client on an existing data dir that has stored user `name`."""
    p = spawn(ctx, role, port, data)
    p.expect(event="started", user=name)
    return p


def try_accept(peer, timeout):
    """Accept a connection, returning None if none arrives within `timeout`."""
    try:
        return peer.accept(timeout)
    except Exception:
        return None


def handshake(peer, name, timeout=5, welcome=True):
    """Accept the next client connection, check its hello, optionally answer welcome."""
    conn = peer.accept(timeout)
    hello = conn.recv()
    ensure(isinstance(hello, dict) and hello.get("type") == "hello",
           f"first frame must be hello, got {hello!r}")
    ensure(hello.get("user") == name and hello.get("protocol") == 1,
           f"hello must carry user={name!r}, protocol=1; got {hello!r}")
    if welcome:
        conn.send({"type": "welcome", "user": name, "protocol": 1})
    return conn


def peer_client(ctx, peer, data, name, role="default", json_mode=True):
    """Client against a scripted peer, logged in and ONLINE. Returns (proc, conn)."""
    p = ctx.spawn(role, args=client_args(peer.port, data, json_mode), json_lines=json_mode)
    if json_mode:
        p.expect(event="started", user=None)
    p.send(f"login {name}")
    conn = handshake(peer, name)
    if json_mode:
        p.expect(event="status", online=True)
    return p, conn


def wait_closed(conn, timeout=5):
    """True if the client closes the peer connection within `timeout` seconds."""
    sock = conn.sock
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            data = sock.recv(65536)
        except socket.timeout:
            return False
        except OSError:
            return True
        if not data:
            return True
    return False


def deliver(id_, frm, to, body, seq):
    return {"type": "deliver", "id": id_, "from": frm, "to": to, "body": body, "seq": seq}


def sync_deliver(p, conn, name, seq=900):
    """Send a valid deliver and wait for both its message event and its delivered frame.
    Since frames are processed in order, everything sent before it has been handled."""
    sid = new_id()
    conn.send(deliver(sid, "syncer", name, "sync", seq))
    p.expect(event="message", id=sid)
    frame = conn.recv_match(type="delivered")
    return sid, frame


class Upstream:
    """Minimal NDJSON client socket to the real server (used as a man-in-the-middle)."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = b""

    def send(self, obj):
        self.sock.sendall(json.dumps(obj).encode("utf-8") + b"\n")

    def recv(self, timeout=5):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TestFailure("timed out waiting for a frame from the server")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(65536)
            if not chunk:
                raise TestFailure("server closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    def recv_match(self, timeout=5, **fields):
        deadline = time.monotonic() + timeout
        while True:
            frame = self.recv(max(0.05, deadline - time.monotonic()))
            if all(frame.get(k) == v for k, v in fields.items()):
                return frame

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
