"""Output formats: JSON event shapes and human-mode lines."""
import re

from friendkit import test

from helpers import WELCOME_TEXT, ensure, new_id, client_args, spawn, online, handshake, peer_client, deliver

SHAPES = {
    "started": {"user": (str, type(None))},
    "logged_in": {"user": (str,)},
    "status": {"online": (bool,)},
    "queued": {"id": (str,), "to": (str,), "body": (str,)},
    "sent": {"id": (str,), "seq": (int,)},
    "failed": {"id": (str,), "code": (str,)},
    "message": {"id": (str,), "from": (str,), "to": (str,), "body": (str,), "seq": (int,)},
    "inbox": {"messages": (list,)},
    "outbox": {"messages": (list,)},
    "error": {"code": (str,), "detail": (str,)},
}
INBOX_ITEM = {"id": str, "from": str, "to": str, "body": str, "seq": int}
OUTBOX_ITEM = {"id": str, "to": str, "body": str}


def _check_item(item, shape, where):
    ensure(isinstance(item, dict) and set(item) == set(shape), f"{where}: bad item keys {item!r}")
    for k, t in shape.items():
        ok = isinstance(item[k], t) and not (t is int and isinstance(item[k], bool))
        ensure(ok, f"{where}: field {k} has wrong type in {item!r}")


def _check_record(r):
    ensure("__invalid_json__" not in r, f"stdout line is not JSON: {r.get('__invalid_json__')!r}")
    ev = r.get("event")
    ensure(ev in SHAPES, f"unknown or missing event in {r!r}")
    shape = SHAPES[ev]
    ensure(set(r) == set(shape) | {"event"}, f"event {ev} must have exactly fields {sorted(shape)}: {r!r}")
    for k, types in shape.items():
        v = r[k]
        ok = isinstance(v, types) and not (int in types and bool not in types and isinstance(v, bool))
        ensure(ok, f"event {ev}: field {k} has wrong type: {r!r}")
    if ev == "inbox":
        for m in r["messages"]:
            _check_item(m, INBOX_ITEM, "inbox")
    if ev == "outbox":
        for m in r["messages"]:
            _check_item(m, OUTBOX_ITEM, "outbox")


@test("I-8", "I-9", "C-17", "C-18", "C-14")
def json_event_shapes(ctx):
    """Every stdout line in --json mode is one JSON object of a defined event shape."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    p.send("send bob one")
    q1 = p.expect(event="queued")
    p.send("send bob two")
    q2 = p.expect(event="queued")
    conn.recv_match(type="send")
    conn.recv_match(type="send")
    p.send("outbox")
    p.expect(event="outbox")
    conn.send({"type": "ack", "id": q1["id"], "seq": 1})
    p.expect(event="sent", id=q1["id"])
    conn.send({"type": "error", "code": "invalid_body", "detail": "x", "id": q2["id"]})
    p.expect(event="failed", id=q2["id"])
    mid = new_id()
    conn.send(deliver(mid, "bob", "alice", "yo", 2))
    p.expect(event="message", id=mid)
    p.send("inbox")
    p.expect(event="inbox")
    p.send("login alice")
    p.expect(event="error", code="already_logged_in")
    conn.send({"type": "error", "code": "custom", "detail": "d"})
    p.expect(event="error", code="custom")
    conn.close()
    p.expect(event="status", online=False)
    seen = set()
    for r in p.records:
        _check_record(r)
        seen.add(r["event"])
    ensure(seen == set(SHAPES), f"scenario should produce every event, got {sorted(seen)}")


def _human(ctx, port, data, role="default"):
    return ctx.spawn(role, args=client_args(port, data, json_mode=False), json_lines=False)


def _err(code):
    return re.compile(r"! " + re.escape(code) + r"(: .+)?")


@test("I-10", "C-3", "C-5", "C-9", "C-12", "C-14")
def human_mode_basic(ctx):
    """Human mode prints the welcome, login, status, queued, sent, empty-box and error lines."""
    srv = ctx.service("server")
    proxy = ctx.proxy(srv.port)
    p = _human(ctx, proxy.port, ctx.path("a"))
    p.expect_line(WELCOME_TEXT)
    p.send("login alice")
    p.expect_line("Logged in as alice")
    p.expect_line("* online")
    p.send("send bob hi  there")
    p.expect_line("[queued] to bob: hi  there")
    p.expect_line("[sent] to bob: hi  there")
    p.send("outbox")
    p.expect_line("(outbox empty)")
    p.send("inbox")
    p.expect_line("(inbox empty)")
    p.send("frobnicate")
    p.expect_line(_err("unknown_command"))
    p.send("login x.y")
    p.expect_line(_err("already_logged_in"))
    proxy.down()
    p.expect_line("* offline")
    ensure(not any(l.lstrip().startswith("{") for l in p.lines), "human mode must not print JSON")


@test("I-10", "C-3", "C-18", roles=("alice", "bob"), interop=True)
def human_mode_receive(ctx):
    """Human mode prints received messages and inbox lines, and 'Logged in as' on restart."""
    srv = ctx.service("server")
    a = online(ctx, "alice", srv.port, ctx.path("a"), "alice")
    bdata = ctx.path("b")
    b = _human(ctx, srv.port, bdata, "bob")
    b.expect_line(WELCOME_TEXT)
    b.send("login bob")
    b.expect_line("Logged in as bob")
    b.expect_line("* online")
    a.send("send bob hello  bob")
    a.send("send bob second")
    b.expect_line("[alice] hello  bob")
    b.expect_line("[alice] second")
    b.send("inbox")
    b.expect_line("[alice] hello  bob")
    b.expect_line("[alice] second")
    b.send("quit")
    ensure(b.exit_code(timeout=5) == 0, "quit must exit 0")
    b2 = _human(ctx, srv.port, bdata, "bob")
    b2.expect_line("Logged in as bob")
    b2.expect_line("* online")


@test("I-10", "C-17", "C-20")
def human_mode_peer_lines(ctx):
    """Human mode prints failed, pass-through errors (with/without detail), outbox and message lines."""
    peer = ctx.peer()
    p = _human(ctx, peer.port, ctx.path("a"))
    p.expect_line(WELCOME_TEXT)
    p.send("login alice")
    p.expect_line("Logged in as alice")
    conn = handshake(peer, "alice")
    p.expect_line("* online")
    p.send("send bob doomed msg")
    p.expect_line("[queued] to bob: doomed msg")
    f = conn.recv_match(type="send")
    conn.send({"type": "error", "code": "invalid_body", "detail": "no", "id": f["id"]})
    p.expect_line("[failed:invalid_body] to bob: doomed msg")
    conn.send({"type": "error", "code": "quota", "detail": "too many"})
    p.expect_line("! quota: too many")
    conn.send({"type": "error", "code": "quota2"})
    p.expect_line("! quota2")
    p.send("send bob a")
    p.expect_line("[queued] to bob: a")
    p.send("send carol b  c")
    p.expect_line("[queued] to carol: b  c")
    p.send("outbox")
    p.expect_line("[queued] to bob: a")
    p.expect_line("[queued] to carol: b  c")
    conn.send(deliver(new_id(), "bob", "alice", "hey there", 5))
    p.expect_line("[bob] hey there")
    p.send("inbox")
    p.expect_line("[bob] hey there")
