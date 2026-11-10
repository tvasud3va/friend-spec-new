"""Durable state across kills (SIGKILL) and restarts."""
from friendkit import test

from helpers import ensure, new_id, client_args, spawn, fresh, online, restart, handshake, peer_client, deliver

OFFLINE = ["--server", "127.0.0.1:1"]


@test("C-1", "C-2", "C-4")
def user_survives_kill(ctx):
    """A kill right after logged_in keeps the user; restart needs no login."""
    data = ctx.path("a")
    p = ctx.spawn(args=client_args(None, data, extra=OFFLINE))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    p.kill()
    p2 = ctx.spawn(args=client_args(None, data, extra=OFFLINE))
    p2.expect(event="started", user="alice")
    p2.send("login bob")
    p2.expect(event="error", code="already_logged_in")


@test("C-1", "C-2", "C-12")
def outbox_survives_kill(ctx):
    """A kill right after queued keeps the outbox, in order, with the same IDs."""
    data = ctx.path("a")
    p = ctx.spawn(args=client_args(None, data, extra=OFFLINE))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.send("send bob first")
    q1 = p.expect(event="queued", body="first")
    p.send("send carol second one")
    q2 = p.expect(event="queued", body="second one")
    p.kill()
    p2 = ctx.spawn(args=client_args(None, data, extra=OFFLINE))
    p2.expect(event="started", user="alice")
    p2.send("outbox")
    p2.expect(event="outbox", messages=[{"id": q1["id"], "to": "bob", "body": "first"},
                                        {"id": q2["id"], "to": "carol", "body": "second one"}])


@test("C-1", "C-2", "C-14", "C-15", "C-4")
def queued_offline_sent_after_restart(ctx):
    """Messages queued offline, then a kill, are sent with their original IDs once the server is reachable."""
    srv = ctx.service("server")
    proxy = ctx.proxy(srv.port)
    proxy.down()
    data = ctx.path("a")
    p = fresh(ctx, "default", proxy.port, data, "alice")
    p.send("send bob kept")
    q = p.expect(event="queued", body="kept")
    p.kill()
    proxy.up()
    p2 = restart(ctx, "default", proxy.port, data, "alice")
    p2.expect(event="status", online=True)
    p2.expect(event="sent", id=q["id"], seq=1)
    p2.send("outbox")
    p2.expect(event="outbox", messages=[])


@test("C-2", "C-14", "C-1")
def sent_removal_survives_kill(ctx):
    """A kill right after sent leaves the outbox empty on restart, and nothing is resent."""
    peer = ctx.peer()
    data = ctx.path("a")
    p, conn = peer_client(ctx, peer, data, "alice")
    p.send("send bob once")
    q = p.expect(event="queued")
    conn.recv_match(type="send")
    conn.send({"type": "ack", "id": q["id"], "seq": 1})
    p.expect(event="sent", id=q["id"], seq=1)
    p.kill()
    conn.close()
    p2 = ctx.spawn(args=client_args(peer.port, data))
    p2.expect(event="started", user="alice")
    conn2 = handshake(peer, "alice")
    p2.expect(event="status", online=True)
    p2.send("outbox")
    p2.expect(event="outbox", messages=[])
    p2.send("send bob next")
    q2 = p2.expect(event="queued", body="next")
    f = conn2.recv_match(type="send")
    ensure(f["id"] == q2["id"], f"an acked message must not be resent after restart, got {f!r}")


@test("C-2", "C-17", "C-1")
def failed_removal_survives_kill(ctx):
    """A kill right after failed leaves the outbox empty on restart."""
    peer = ctx.peer()
    data = ctx.path("a")
    p, conn = peer_client(ctx, peer, data, "alice")
    p.send("send bob bad")
    q = p.expect(event="queued")
    conn.recv_match(type="send")
    conn.send({"type": "error", "code": "invalid_body", "detail": "", "id": q["id"]})
    p.expect(event="failed", id=q["id"], code="invalid_body")
    p.kill()
    p2 = ctx.spawn(args=client_args(None, data, extra=OFFLINE))
    p2.expect(event="started", user="alice")
    p2.send("outbox")
    p2.expect(event="outbox", messages=[])


@test("C-2", "C-18", "C-1")
def inbox_written_before_delivered(ctx):
    """A kill right after the delivered frame still finds the message in the inbox."""
    peer = ctx.peer()
    data = ctx.path("a")
    p, conn = peer_client(ctx, peer, data, "alice")
    mid = new_id()
    conn.send(deliver(mid, "bob", "alice", "keep me", 4))
    d = conn.recv_match(type="delivered")
    ensure(d.get("id") == mid, f"expected delivered, got {d!r}")
    p.kill()
    p2 = ctx.spawn(args=client_args(None, data, extra=OFFLINE))
    p2.expect(event="started", user="alice")
    p2.send("inbox")
    p2.expect(event="inbox", messages=[{"id": mid, "from": "bob", "to": "alice", "body": "keep me", "seq": 4}])


@test("C-1", "C-2", "C-18")
def inbox_survives_kill_after_message(ctx):
    """A kill right after message keeps all received messages in arrival order."""
    peer = ctx.peer()
    data = ctx.path("a")
    p, conn = peer_client(ctx, peer, data, "alice")
    ids = [new_id(), new_id()]
    conn.send(deliver(ids[0], "bob", "alice", "m1", 8))
    conn.send(deliver(ids[1], "carol", "alice", "m2", 3))
    p.expect(event="message", id=ids[1])
    p.kill()
    p2 = ctx.spawn(args=client_args(None, data, extra=OFFLINE))
    p2.expect(event="started", user="alice")
    p2.send("inbox")
    p2.expect(event="inbox", messages=[
        {"id": ids[0], "from": "bob", "to": "alice", "body": "m1", "seq": 8},
        {"id": ids[1], "from": "carol", "to": "alice", "body": "m2", "seq": 3},
    ])
