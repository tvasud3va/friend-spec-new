"""Multi-party scenarios through the real server (interop across targets)."""
from friendkit import test

from helpers import ensure, client_args, spawn, fresh, online, restart, handshake, Upstream


@test("P-1", "P-21", "P-15", "P-19", "P-10", "C-14", "C-18", "P-9", roles=("alice", "bob"), interop=True)
def live_message(ctx):
    """Both online: alice's message reaches bob with the same id and seq, from alice."""
    srv = ctx.service("server")
    a = online(ctx, "alice", srv.port, ctx.path("a"), "alice")
    b = online(ctx, "bob", srv.port, ctx.path("b"), "bob")
    a.send("send bob Hi Bob, I have something important to tell you")
    q = a.expect(event="queued", to="bob")
    s = a.expect(event="sent", id=q["id"])
    m = b.expect(event="message", id=q["id"])
    ensure(m == {"event": "message", "id": q["id"], "from": "alice", "to": "bob",
                 "body": "Hi Bob, I have something important to tell you", "seq": s["seq"]},
           f"message must match the sent one, got {m!r}")
    ensure(s["seq"] == 1, f"first message on a fresh server has seq 1, got {s!r}")
    b.send("send alice What is it?")
    q2 = b.expect(event="queued")
    s2 = b.expect(event="sent", id=q2["id"])
    a.expect(event="message", id=q2["id"], body="What is it?", seq=s2["seq"], **{"from": "bob"})
    ensure(s2["seq"] == 2, f"seq must increase globally, got {s2!r}")


@test("P-18", "P-13", "P-23", "C-23", "P-10", "C-22", roles=("alice", "bob"), interop=True)
def offline_recipient_gets_pending_in_order(ctx):
    """Messages to a never-connected user are held and delivered in seq order on first connect."""
    srv = ctx.service("server")
    a = online(ctx, "alice", srv.port, ctx.path("a"), "alice")
    bodies = ["first", "second", "third"]
    for body in bodies:
        a.send(f"send bob {body}")
    a.expect_where(lambda r: r.get("event") == "sent" and len([x for x in a.all(event="sent")]) >= 3,
                   5, "three sent events")
    queued = [q["id"] for q in a.all(event="queued")]
    sent = a.all(event="sent")
    ensure([s["id"] for s in sent] == queued, f"sent must follow outbox order: {sent!r}")
    seqs = [s["seq"] for s in sent]
    ensure(seqs == [1, 2, 3], f"seqs must be 1,2,3 on a fresh server, got {seqs!r}")
    b = online(ctx, "bob", srv.port, ctx.path("b"), "bob")
    b.expect_where(lambda r: r.get("event") == "message" and r.get("id") == queued[2], 5, "third message")
    msgs = b.all(event="message")
    ensure([(m["id"], m["body"], m["seq"]) for m in msgs] == list(zip(queued, bodies, seqs)),
           f"pending messages must arrive once each in seq order, got {msgs!r}")


@test("P-22", "C-22", "C-23", "C-13", roles=("alice", "bob"), interop=True)
def burst_keeps_order(ctx):
    """A burst of sends gets increasing seqs and is received in that order."""
    srv = ctx.service("server")
    a = online(ctx, "alice", srv.port, ctx.path("a"), "alice")
    b = online(ctx, "bob", srv.port, ctx.path("b"), "bob")
    n = 10
    a.send_raw("".join(f"send bob burst {i}\n" for i in range(n)))
    b.expect_where(lambda r: r.get("event") == "message" and r.get("body") == f"burst {n - 1}", 5, "last")
    a.expect_where(lambda r: r.get("event") == "sent" and len(a.all(event="sent")) >= n, 5, "all sent")
    queued = a.all(event="queued")
    ensure([q["body"] for q in queued] == [f"burst {i}" for i in range(n)], "queued must follow command order")
    sent = a.all(event="sent")
    ensure([s["id"] for s in sent] == [q["id"] for q in queued], f"sent must follow outbox order: {sent!r}")
    seqs = [s["seq"] for s in sent]
    ensure(all(x < y for x, y in zip(seqs, seqs[1:])), f"seqs must strictly increase: {seqs!r}")
    msgs = b.all(event="message")
    ensure([m["id"] for m in msgs] == [q["id"] for q in queued], f"bob must receive in order: {msgs!r}")


@test("P-23", "C-4", "C-1", "C-19", roles=("alice", "bob"), interop=True)
def recipient_restart_gets_new_messages_only(ctx):
    """A restarted recipient gets messages sent while it was down, without repeats; inbox keeps all."""
    srv = ctx.service("server")
    a = online(ctx, "alice", srv.port, ctx.path("a"), "alice")
    bdata = ctx.path("b")
    b = online(ctx, "bob", srv.port, bdata, "bob")
    a.send("send bob before")
    q1 = a.expect(event="queued")
    b.expect(event="message", id=q1["id"])
    b.send("quit")
    ensure(b.exit_code(timeout=5) == 0, "quit must exit 0")
    a.send("send bob after")
    q2 = a.expect(event="queued")
    a.expect(event="sent", id=q2["id"])
    b2 = restart(ctx, "bob", srv.port, bdata, "bob")
    b2.expect(event="message", id=q2["id"], body="after")
    b2.send("inbox")
    ib = b2.expect(event="inbox")
    ensure([m["id"] for m in ib["messages"]] == [q1["id"], q2["id"]], f"inbox must hold both once: {ib!r}")
    ensure(not b2.all(event="message", id=q1["id"]), "an already delivered message must not reappear")


@test("P-23", "C-7", "C-9", roles=("alice", "bob"), interop=True)
def recipient_connection_lost(ctx):
    """A message sent while the recipient's connection is down reaches it after reconnect."""
    srv = ctx.service("server")
    bproxy = ctx.proxy(srv.port)
    a = online(ctx, "alice", srv.port, ctx.path("a"), "alice")
    b = online(ctx, "bob", bproxy.port, ctx.path("b"), "bob")
    bproxy.down()
    b.expect(event="status", online=False)
    a.send("send bob while you were away")
    q = a.expect(event="queued")
    a.expect(event="sent", id=q["id"])
    bproxy.up()
    b.expect(event="status", online=True)
    b.expect(event="message", id=q["id"], body="while you were away")


@test("C-12", "C-13", "C-15", "C-18", "P-13", "P-18", "P-21", "P-23", "C-9",
      roles=("alice", "bob"), interop=True)
def assignment_scenario(ctx):
    """The D-8 scenario: chat, both go offline and queue, reconnect alternately."""
    srv = ctx.service("server")
    ap, bp = ctx.proxy(srv.port), ctx.proxy(srv.port)
    a = online(ctx, "alice", ap.port, ctx.path("a"), "alice")
    b = online(ctx, "bob", bp.port, ctx.path("b"), "bob")
    a.send("send bob Hi Bob, I have something important to tell you")
    q1 = a.expect(event="queued")
    b.expect(event="message", id=q1["id"], body="Hi Bob, I have something important to tell you")
    b.send("send alice What is it?")
    q2 = b.expect(event="queued")
    a.expect(event="message", id=q2["id"], body="What is it?")
    # (3) alice offline, queues
    ap.down()
    a.expect(event="status", online=False)
    a.send("send bob I won the lottery")
    q3 = a.expect(event="queued")
    # (4) bob offline, queues
    bp.down()
    b.expect(event="status", online=False)
    b.send("send alice Are you there?")
    q4 = b.expect(event="queued")
    # (5) alice reconnects, her message reaches the server
    ap.up()
    a.expect(event="sent", id=q3["id"])
    # (6) alice offline again
    ap.down()
    a.expect(event="status", online=False)
    # (7) bob reconnects: receives alice's message, delivers his own
    bp.up()
    b.expect(event="message", id=q3["id"], body="I won the lottery", **{"from": "alice"})
    b.expect_where(lambda r: r.get("event") == "sent" and r.get("id") == q4["id"], 5, "bob's queued sent")
    ap.up()
    a.expect(event="message", id=q4["id"], body="Are you there?", **{"from": "bob"})
    ensure(len(b.all(event="message", id=q3["id"])) == 1, "each message is shown exactly once")


@test("P-20", "C-15", "C-14", "P-9", roles=("alice", "bob"), interop=True)
def lost_ack_retry_is_idempotent(ctx):
    """If the ack is lost, the retry uses the same id, gets the original seq, and is delivered once."""
    srv = ctx.service("server")
    peer = ctx.peer()
    a = spawn(ctx, "alice", peer.port, ctx.path("a"))
    a.expect(event="started", user=None)
    a.send("login alice")
    c1 = peer.accept(5)
    up1 = Upstream(srv.port)
    up1.send(c1.recv())
    c1.send(up1.recv_match(type="welcome"))
    a.expect(event="status", online=True)
    a.send("send bob exactly once")
    q = a.expect(event="queued")
    f1 = c1.recv_match(type="send")
    up1.send(f1)
    ack1 = up1.recv_match(type="ack")
    up1.close()
    c1.close()  # the ack is lost
    a.expect(event="status", online=False)
    c2 = peer.accept(5)
    up2 = Upstream(srv.port)
    up2.send(c2.recv())
    c2.send(up2.recv_match(type="welcome"))
    a.expect(event="status", online=True)
    f2 = c2.recv_match(type="send")
    ensure(f2["id"] == f1["id"] == q["id"], f"the retry must reuse the id: {f1!r} vs {f2!r}")
    up2.send(f2)
    ack2 = up2.recv_match(type="ack")
    ensure(ack2.get("seq") == ack1.get("seq"), f"retry must get the original seq: {ack1!r} vs {ack2!r}")
    c2.send(ack2)
    a.expect(event="sent", id=q["id"], seq=ack1["seq"])
    b = online(ctx, "bob", srv.port, ctx.path("b"), "bob")
    b.expect(event="message", id=q["id"], body="exactly once")
    b.assert_none(1.5, event="message")
    up2.close()


@test("P-7", "C-11", roles=("alice", "Alice"), interop=True)
def names_case_sensitive(ctx):
    """'alice' and 'Alice' are different users who can message each other."""
    srv = ctx.service("server")
    lo = online(ctx, "alice", srv.port, ctx.path("lo"), "alice")
    up = online(ctx, "Alice", srv.port, ctx.path("up"), "Alice")
    lo.send("send Alice hello twin")
    q = lo.expect(event="queued", to="Alice")
    up.expect(event="message", id=q["id"], to="Alice", **{"from": "alice"})
    ensure(not lo.all(event="error"), "no replaced/invalid errors expected")
    ensure(not up.all(event="error"), "no replaced/invalid errors expected")
