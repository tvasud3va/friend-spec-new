"""Login, startup, local validation of names and bodies, queuing."""
from friendkit import test

from helpers import UUID4, ensure, client_args, spawn, fresh, online, restart

OFFLINE = ["--server", "127.0.0.1:1"]


def _offline(ctx, data):
    return ctx.spawn(args=client_args(None, data, extra=OFFLINE))


@test("C-3", "I-5", "I-8")
def started_is_first_event(ctx):
    """On a fresh data dir the first output is started with user null."""
    p = _offline(ctx, ctx.path("a"))
    first = p.expect_where(lambda r: True, 5, "first line")
    ensure(first == {"event": "started", "user": None}, f"first line must be started/null, got {first!r}")


@test("C-5", "C-3", "C-4", "C-9")
def login_then_connect(ctx):
    """login persists the name, emits logged_in and connects; no status before ONLINE."""
    srv = ctx.service("server")
    p = spawn(ctx, "default", srv.port, ctx.path("a"))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    p.expect(event="status", online=True)
    statuses = p.all(event="status")
    ensure(statuses == [{"event": "status", "online": True}],
           f"only one status (online) expected, got {statuses!r}")


@test("C-3", "C-4", "C-1")
def restart_with_stored_user(ctx):
    """With a stored user, started carries it and the client connects without login."""
    srv = ctx.service("server")
    data = ctx.path("a")
    p = online(ctx, "default", srv.port, data, "alice")
    p.send("quit")
    ensure(p.exit_code(timeout=5) == 0, "quit must exit 0")
    p2 = spawn(ctx, "default", srv.port, data)
    first = p2.expect_where(lambda r: True, 5, "first line")
    ensure(first == {"event": "started", "user": "alice"}, f"first line must be started/alice, got {first!r}")
    p2.expect(event="status", online=True)
    ensure(not p2.all(event="logged_in"), "no logged_in on restart")


@test("C-6", "I-9")
def login_twice(ctx):
    """A second login emits already_logged_in and changes nothing."""
    srv = ctx.service("server")
    data = ctx.path("a")
    p = online(ctx, "default", srv.port, data, "alice")
    p.send("login bob")
    p.expect(event="error", code="already_logged_in")
    p.send("login alice")
    p.expect(event="error", code="already_logged_in")
    p.send("send bob hi")
    p.expect(event="queued", to="bob", body="hi")
    p.send("send alice hi")
    p.expect(event="error", code="invalid_recipient")
    p.send("quit")
    p.exit_code(timeout=5)
    restart(ctx, "default", srv.port, data, "alice")


@test("C-5", "P-7", "I-9")
def invalid_names_rejected(ctx):
    """Names outside ^[A-Za-z0-9_-]{1,32}$ are rejected with invalid_name and nothing is stored."""
    data = ctx.path("a")
    p = _offline(ctx, data)
    p.expect(event="started", user=None)
    bad = ["a" * 33, "a.b", "a/b", "café", "ａ", "ab٣", "a@b", "a+b", "a b"]
    for name in bad:
        p.send(f"login {name}")
        p.expect(event="error", code="invalid_name")
    ensure(not p.all(event="logged_in"), "no invalid name may log in")
    p.send("send bob hi")
    p.expect(event="error", code="not_logged_in")
    p.send("quit")
    p.exit_code(timeout=5)
    p2 = _offline(ctx, data)
    p2.expect(event="started", user=None)


@test("C-5", "P-7")
def valid_name_edges(ctx):
    """Single-char and 32-char names using every allowed class are valid."""
    name = "Az09_-" + "x" * 26
    p = _offline(ctx, ctx.path("a"))
    p.expect(event="started", user=None)
    p.send(f"login {name}")
    p.expect(event="logged_in", user=name)
    p2 = _offline(ctx, ctx.path("b"))
    p2.expect(event="started", user=None)
    p2.send("login _")
    p2.expect(event="logged_in", user="_")


@test("C-11", "I-9")
def send_validation_order(ctx):
    """send validates: not_logged_in, then invalid_recipient (incl. self), then invalid_body; nothing queued."""
    p = _offline(ctx, ctx.path("a"))
    p.expect(event="started", user=None)
    p.send("send b.c    ")
    p.expect(event="error", code="not_logged_in")
    p.send("send bob hi")
    p.expect(event="error", code="not_logged_in")
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    p.send("send b.c    ")
    p.expect(event="error", code="invalid_recipient")
    p.send("send alice    ")
    p.expect(event="error", code="invalid_recipient")
    p.send("send alice hi")
    p.expect(event="error", code="invalid_recipient")
    p.send("send " + "b" * 33 + " hi")
    p.expect(event="error", code="invalid_recipient")
    p.send("send bob    ")
    p.expect(event="error", code="invalid_body")
    p.send("outbox")
    p.expect(event="outbox", messages=[])
    ensure(not p.all(event="queued"), "no invalid send may be queued")


@test("C-11", "P-7")
def recipient_is_case_sensitive(ctx):
    """'Alice' is a different user from 'alice', so it is a valid recipient for alice."""
    p = _offline(ctx, ctx.path("a"))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    p.send("send Alice hi")
    p.expect(event="queued", to="Alice", body="hi")


WS_ALL = "\t\x0b\x0c\r \x85\xa0         　"


@test("P-8", "C-11")
def whitespace_only_bodies_invalid(ctx):
    """Bodies made only of Unicode White_Space code points are invalid."""
    p = _offline(ctx, ctx.path("a"))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    for body in [" ", "\t", "　", " ", " ", "\x85", "\x0b\x0c", WS_ALL]:
        p.send("send bob " + body)
        p.expect(event="error", code="invalid_body")
    p.send("outbox")
    p.expect(event="outbox", messages=[])


@test("P-8", "C-12")
def non_whitespace_lookalikes_valid(ctx):
    """U+001C-U+001F, U+180E, U+200B and U+FEFF are non-whitespace, so these bodies are valid and verbatim."""
    p = _offline(ctx, ctx.path("a"))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    bodies = ["\x1c", "\x1f", "᠎", "​", "﻿", "  ​  ", WS_ALL + "x" + WS_ALL]
    for body in bodies:
        p.send("send bob " + body)
        p.expect(event="queued", to="bob", body=body)
    ensure(not p.all(event="error"), "none of these bodies is invalid")


@test("P-8", "C-11", "C-12")
def body_length_in_code_points(ctx):
    """Body length limit is 4000 code points, counting non-BMP characters as one."""
    p = _offline(ctx, ctx.path("a"))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    p.send("send bob " + "a" * 4000)
    p.expect(event="queued", body="a" * 4000)
    p.send("send bob " + "\U0001F600" * 4000)
    p.expect(event="queued", body="\U0001F600" * 4000)
    p.send("send bob " + "é" * 4000)
    p.expect(event="queued", body="é" * 4000)
    p.send("send bob " + "a" * 4001)
    p.expect(event="error", code="invalid_body")
    p.send("send bob " + "\U0001F600" * 4001)
    p.expect(event="error", code="invalid_body")
    ensure(len(p.all(event="queued")) == 3, "exactly the three 4000-code-point bodies are queued")


@test("C-12", "P-9", "I-8")
def queued_ids_are_uuid4(ctx):
    """Each queued message gets a fresh lowercase UUIDv4 and is appended to the outbox."""
    p = _offline(ctx, ctx.path("a"))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    ids = []
    for i in range(5):
        p.send(f"send bob msg {i}")
        q = p.expect(event="queued", to="bob", body=f"msg {i}")
        ensure(isinstance(q.get("id"), str) and UUID4.match(q["id"]),
               f"id must be a lowercase UUIDv4, got {q.get('id')!r}")
        ids.append(q["id"])
    ensure(len(set(ids)) == 5, "ids must be unique")
    p.send("outbox")
    ob = p.expect(event="outbox")
    ensure(ob["messages"] == [{"id": i, "to": "bob", "body": f"msg {n}"} for n, i in enumerate(ids)],
           f"outbox must hold all five in order, got {ob['messages']!r}")


@test("C-12", "C-13", "C-14", "P-19", "P-10")
def queued_same_online_and_offline(ctx):
    """A send while ONLINE is queued just like offline, then sent with the server's seq."""
    srv = ctx.service("server")
    p = online(ctx, "default", srv.port, ctx.path("a"), "alice")
    p.send("send bob hello")
    q = p.expect(event="queued", to="bob", body="hello")
    s = p.expect(event="sent", id=q["id"])
    ensure(s.get("seq") == 1, f"the first message on a fresh server gets seq 1, got {s!r}")
    p.send("outbox")
    p.expect(event="outbox", messages=[])
