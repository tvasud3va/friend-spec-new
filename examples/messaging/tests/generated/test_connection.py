"""Connection lifecycle: retry, status events, replacement."""
import time

from friendkit import test

from helpers import ensure, client_args, spawn, fresh, online, restart, handshake, peer_client, try_accept, wait_closed


@test("C-7", "C-9")
def offline_until_server_reachable(ctx):
    """While the server is unreachable no status is emitted; the client connects once it is back."""
    srv = ctx.service("server")
    proxy = ctx.proxy(srv.port)
    proxy.down()
    p = fresh(ctx, "default", proxy.port, ctx.path("a"), "alice")
    p.assert_none(2.0, event="status")
    proxy.up()
    p.expect(event="status", online=True)
    statuses = p.all(event="status")
    ensure(len(statuses) == 1, f"failed attempts must not emit status, got {statuses!r}")


@test("C-7", "C-9", "C-15", "C-13")
def connection_lost_and_restored(ctx):
    """Losing the connection emits offline; queued messages go out after it comes back."""
    srv = ctx.service("server")
    proxy = ctx.proxy(srv.port)
    p = online(ctx, "default", proxy.port, ctx.path("a"), "alice")
    proxy.down()
    p.expect(event="status", online=False)
    p.send("send bob while offline")
    q = p.expect(event="queued", body="while offline")
    p.assert_none(1.5, event="sent")
    proxy.up()
    p.expect(event="status", online=True)
    p.expect(event="sent", id=q["id"], seq=1)
    statuses = [s["online"] for s in p.all(event="status")]
    ensure(statuses == [True, False, True], f"expected online, offline, online; got {statuses!r}")


@test("C-7")
def retries_indefinitely(ctx):
    """The client keeps retrying through repeated outages."""
    srv = ctx.service("server")
    proxy = ctx.proxy(srv.port)
    p = online(ctx, "default", proxy.port, ctx.path("a"), "alice")
    for _ in range(2):
        proxy.down()
        p.expect(event="status", online=False)
        time.sleep(2.0)
        proxy.up()
        p.expect(event="status", online=True)


@test("C-10", "C-9", "I-9")
def replaced_stops_client(ctx):
    """error replaced: report it, go offline, close, and never reconnect until restarted."""
    peer = ctx.peer()
    data = ctx.path("a")
    p, conn = peer_client(ctx, peer, data, "alice")
    conn.send({"type": "error", "code": "replaced", "detail": "logged in elsewhere"})
    p.expect(event="error", code="replaced", detail="logged in elsewhere")
    p.expect(event="status", online=False)
    ensure(wait_closed(conn, 5), "the client must close the replaced connection")
    ensure(try_accept(peer, 2.5) is None, "a replaced client must not reconnect")
    p.send("send bob still works")
    p.expect(event="queued", body="still works")
    ensure(try_accept(peer, 1.0) is None, "a replaced client must not reconnect after a send")
    p.send("quit")
    ensure(p.exit_code(timeout=5) == 0, "quit must exit 0")
    p2 = spawn(ctx, "default", peer.port, data)
    p2.expect(event="started", user="alice")
    handshake(peer, "alice")
    p2.expect(event="status", online=True)


@test("C-10", "C-26", "C-24")
def replaced_while_connecting(ctx):
    """replaced before welcome: error with empty detail, no status event, no reconnect."""
    peer = ctx.peer()
    p = spawn(ctx, "default", peer.port, ctx.path("a"))
    p.expect(event="started", user=None)
    p.send("login alice")
    conn = handshake(peer, "alice", welcome=False)
    conn.send({"type": "error", "code": "replaced", "detail": 99})
    p.expect(event="error", code="replaced", detail="")
    ensure(try_accept(peer, 2.5) is None, "a replaced client must not reconnect")
    ensure(not p.all(event="status"), "no status event when replaced while CONNECTING")


@test("P-14", "C-10", roles=("alice", "alice2", "bob"), interop=True)
def second_login_replaces_first(ctx):
    """A second client logging in as the same user replaces the first, which stays offline."""
    srv = ctx.service("server")
    a1 = online(ctx, "alice", srv.port, ctx.path("a1"), "alice")
    a2 = online(ctx, "alice2", srv.port, ctx.path("a2"), "alice")
    a1.expect(event="error", code="replaced")
    a1.expect(event="status", online=False)
    b = online(ctx, "bob", srv.port, ctx.path("b"), "bob")
    b.send("send alice to the live one")
    a2.expect(event="message", body="to the live one", **{"from": "bob"})
    a2.assert_none(2.0, event="error", code="replaced")
    ensure(not a1.all(event="message"), "the replaced client must not receive messages")
    ensure(len(a1.all(event="status", online=True)) == 1, "the replaced client must not reconnect")
