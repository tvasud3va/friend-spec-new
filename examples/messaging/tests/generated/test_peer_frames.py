"""Client protocol behaviour against a scripted server (frame by frame)."""
import json
import time

from friendkit import test

from helpers import (UUID4, ensure, new_id, client_args, handshake, peer_client, deliver,
                     sync_deliver, wait_closed)


@test("P-11", "C-8", "C-9", "P-3")
def hello_then_online_on_welcome(ctx):
    """The first frame is hello{user, protocol:1}; ONLINE (status true) only after welcome."""
    peer = ctx.peer()
    p = ctx.spawn(args=client_args(peer.port, ctx.path("a")))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    conn = peer.accept(5)
    hello = conn.recv()
    ensure(hello.get("type") == "hello" and hello.get("user") == "alice" and hello.get("protocol") == 1,
           f"expected hello{{user: alice, protocol: 1}}, got {hello!r}")
    p.assert_none(1.0, event="status")
    conn.send({"type": "welcome", "user": "alice", "protocol": 1})
    p.expect(event="status", online=True)


@test("P-11", "C-13", "C-1")
def hello_first_even_with_outbox(ctx):
    """With a non-empty outbox, the first frame on a connection is still hello."""
    peer = ctx.peer()
    data = ctx.path("a")
    p = ctx.spawn(args=client_args(peer.port, data))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    conn = handshake(peer, "alice", welcome=False)
    p.send("send bob first")
    q = p.expect(event="queued")
    p.kill()
    conn.close()
    p2 = ctx.spawn(args=client_args(peer.port, data))
    p2.expect(event="started", user="alice")
    conn2 = handshake(peer, "alice")
    p2.expect(event="status", online=True)
    f = conn2.recv()
    ensure(f.get("type") == "send" and f.get("id") == q["id"], f"expected the stored send after welcome, got {f!r}")


@test("C-13", "C-22", "P-9")
def outbox_flushed_in_order_on_welcome(ctx):
    """On welcome the whole outbox is transmitted in order; later sends go out immediately."""
    peer = ctx.peer()
    p = ctx.spawn(args=client_args(peer.port, ctx.path("a")))
    p.expect(event="started", user=None)
    p.send("login alice")
    conn = handshake(peer, "alice", welcome=False)
    ids = []
    for body in ["one", "two", "three"]:
        p.send(f"send bob {body}")
        ids.append(p.expect(event="queued", body=body)["id"])
    conn.send({"type": "welcome", "user": "alice", "protocol": 1})
    p.expect(event="status", online=True)
    frames = [conn.recv_match(type="send") for _ in range(3)]
    ensure([f["id"] for f in frames] == ids, f"send frames must follow outbox order, got {frames!r}")
    ensure([(f["to"], f["body"]) for f in frames] == [("bob", "one"), ("bob", "two"), ("bob", "three")],
           "send frames must carry to and body")
    for f in frames:
        ensure(UUID4.match(f["id"]), f"id must be a lowercase UUIDv4: {f['id']!r}")
    p.send("send carol four")
    q4 = p.expect(event="queued", body="four")
    f4 = conn.recv_match(type="send")
    ensure(f4["id"] == q4["id"] and f4["to"] == "carol" and f4["body"] == "four", f"got {f4!r}")
    for i, f in enumerate(frames):
        conn.send({"type": "ack", "id": f["id"], "seq": 10 + i})
    p.expect_where(lambda r: r.get("event") == "sent" and r.get("id") == ids[2], 5, "sent for third")
    sent = p.all(event="sent")
    ensure([(s["id"], s["seq"]) for s in sent] == [(ids[0], 10), (ids[1], 11), (ids[2], 12)],
           f"sent events must follow outbox order with the acked seq, got {sent!r}")


@test("C-14", "I-7")
def ack_removes_from_outbox(ctx):
    """An ack removes the entry and emits sent{id, seq} with the server's seq."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    p.send("send bob hi")
    q = p.expect(event="queued")
    f = conn.recv_match(type="send")
    p.send("outbox")
    p.expect(event="outbox", messages=[{"id": q["id"], "to": "bob", "body": "hi"}])
    conn.send({"type": "ack", "id": f["id"], "seq": 42})
    p.expect(event="sent", id=q["id"], seq=42)
    p.send("outbox")
    p.expect(event="outbox", messages=[])


@test("C-15", "P-9", "C-7", "C-9")
def unacked_retransmitted_with_same_id(ctx):
    """Unacked entries survive a closed connection and are resent with the same IDs after reconnect."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    for body in ["a1", "a2"]:
        p.send(f"send bob {body}")
    first = [conn.recv_match(type="send") for _ in range(2)]
    conn.send({"type": "ack", "id": first[0]["id"], "seq": 1})
    p.expect(event="sent", id=first[0]["id"])
    conn.close()
    p.expect(event="status", online=False)
    conn2 = handshake(peer, "alice")
    p.expect(event="status", online=True)
    again = conn2.recv_match(type="send")
    ensure(again["id"] == first[1]["id"] and again["body"] == "a2",
           f"only the unacked entry must be resent with its original id, got {again!r}")
    conn2.send({"type": "ack", "id": again["id"], "seq": 2})
    p.expect(event="sent", id=again["id"], seq=2)
    ensure(len(p.all(event="queued")) == 2, "a retry must not create a new queued message")


@test("C-16")
def ack_for_unknown_id_ignored(ctx):
    """An ack whose id is not in the outbox is ignored."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    p.send("send bob hi")
    q = p.expect(event="queued")
    conn.recv_match(type="send")
    conn.send({"type": "ack", "id": new_id(), "seq": 5})
    sync_deliver(p, conn, "alice")
    ensure(not p.all(event="sent"), "ack for an unknown id must not emit sent")
    p.send("outbox")
    p.expect(event="outbox", messages=[{"id": q["id"], "to": "bob", "body": "hi"}])
    conn.send({"type": "ack", "id": q["id"], "seq": 6})
    p.expect(event="sent", id=q["id"], seq=6)
    conn.send({"type": "ack", "id": q["id"], "seq": 6})
    sync_deliver(p, conn, "alice", seq=901)
    ensure(len(p.all(event="sent")) == 1, "a repeated ack must be ignored")


@test("C-17", "I-8")
def error_with_id_fails_entry(ctx):
    """An error whose id matches an outbox entry removes it, emits failed, and it is never retried."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    p.send("send bob doomed")
    q = p.expect(event="queued")
    conn.recv_match(type="send")
    conn.send({"type": "error", "code": "invalid_body", "detail": "nope", "id": q["id"]})
    p.expect(event="failed", id=q["id"], code="invalid_body")
    ensure(not p.all(event="error"), "a matched error emits failed, not error")
    p.send("outbox")
    p.expect(event="outbox", messages=[])
    conn.close()
    p.expect(event="status", online=False)
    conn2 = handshake(peer, "alice")
    p.expect(event="status", online=True)
    p.send("send bob later")
    q2 = p.expect(event="queued", body="later")
    f = conn2.recv_match(type="send")
    ensure(f["id"] == q2["id"], f"the failed message must not be retried; first send was {f!r}")


@test("C-20", "C-26", "I-9", "P-4")
def server_errors_passed_through(ctx):
    """Unmatched server errors (including unknown codes) are reported as error{code, detail}."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    conn.send({"type": "error", "code": "unknown_type", "detail": "what?"})
    p.expect(event="error", code="unknown_type", detail="what?")
    conn.send({"type": "error", "code": "brand_new_code", "detail": "d", "extra": [1]})
    p.expect(event="error", code="brand_new_code", detail="d")
    conn.send({"type": "error", "code": "invalid_body", "detail": "x", "id": new_id()})
    p.expect(event="error", code="invalid_body", detail="x")
    ensure(not p.all(event="failed"), "an error for an unknown id is not a failure")


@test("C-26", "C-17", "C-20")
def malformed_error_frames(ctx):
    """Errors without a string code are ignored; bad detail becomes ''; a non-string id matches nothing."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    p.send("send bob keep")
    q = p.expect(event="queued")
    conn.recv_match(type="send")
    conn.send({"type": "error", "detail": "no code", "id": q["id"]})
    conn.send({"type": "error", "code": 5, "detail": "numeric code", "id": q["id"]})
    conn.send({"type": "error", "code": None, "detail": "null code"})
    conn.send({"type": "error", "code": "first"})
    p.expect(event="error", code="first", detail="")
    conn.send({"type": "error", "code": "second", "detail": 17})
    p.expect(event="error", code="second", detail="")
    conn.send({"type": "error", "code": "third", "detail": None, "id": [q["id"]]})
    p.expect(event="error", code="third", detail="")
    conn.send({"type": "error", "code": "fourth", "detail": "x", "id": 12})
    p.expect(event="error", code="fourth", detail="x")
    sync_deliver(p, conn, "alice")
    errors = p.all(event="error")
    ensure([e["code"] for e in errors] == ["first", "second", "third", "fourth"],
           f"exactly the four string-code errors must be reported, got {errors!r}")
    ensure(not p.all(event="failed"), "no outbox entry may fail")
    p.send("outbox")
    p.expect(event="outbox", messages=[{"id": q["id"], "to": "bob", "body": "keep"}])


@test("C-18", "C-19", "I-8")
def deliver_and_dedupe(ctx):
    """deliver stores and emits message, then delivered; a duplicate only re-sends delivered."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    mid = new_id()
    conn.send(deliver(mid, "bob", "alice", "hi alice", 3))
    p.expect(event="message", id=mid, to="alice", body="hi alice", seq=3, **{"from": "bob"})
    d = conn.recv_match(type="delivered")
    ensure(d.get("id") == mid, f"expected delivered for {mid}, got {d!r}")
    conn.send(deliver(mid, "bob", "alice", "hi alice", 3))
    d2 = conn.recv_match(type="delivered")
    ensure(d2.get("id") == mid, f"a duplicate must still be acknowledged with delivered, got {d2!r}")
    sync_deliver(p, conn, "alice")
    ensure(len(p.all(event="message", id=mid)) == 1, "a duplicate must not produce a second message event")
    p.send("inbox")
    ib = p.expect(event="inbox")
    ensure([m["id"] for m in ib["messages"]].count(mid) == 1, f"inbox must contain the message once: {ib!r}")
    ensure(ib["messages"][0] == {"id": mid, "from": "bob", "to": "alice", "body": "hi alice", "seq": 3},
           f"inbox entry must hold the delivered fields, got {ib['messages'][0]!r}")


@test("C-19", "C-1")
def dedupe_survives_restart(ctx):
    """After a restart, a re-delivered message is acknowledged but not shown again."""
    peer = ctx.peer()
    data = ctx.path("a")
    p, conn = peer_client(ctx, peer, data, "alice")
    mid = new_id()
    conn.send(deliver(mid, "bob", "alice", "once", 1))
    p.expect(event="message", id=mid)
    conn.recv_match(type="delivered")
    p.kill()
    conn.close()
    p2 = ctx.spawn(args=client_args(peer.port, data))
    p2.expect(event="started", user="alice")
    conn2 = handshake(peer, "alice")
    p2.expect(event="status", online=True)
    conn2.send(deliver(mid, "bob", "alice", "once", 1))
    d = conn2.recv_match(type="delivered")
    ensure(d.get("id") == mid, f"expected delivered for the duplicate, got {d!r}")
    sync_deliver(p2, conn2, "alice")
    ensure(not p2.all(event="message", id=mid), "the duplicate must not be shown again")
    p2.send("inbox")
    ib = p2.expect(event="inbox")
    ensure([m["id"] for m in ib["messages"]].count(mid) == 1, f"inbox must contain it exactly once: {ib!r}")


@test("C-23", "C-18")
def messages_in_delivery_order(ctx):
    """message events and the inbox follow the order the server delivered, not seq order."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    items = [(new_id(), 7, "seven"), (new_id(), 2, "two"), (new_id(), 9, "nine"), (new_id(), 4, "four")]
    for mid, seq, body in items:
        conn.send(deliver(mid, "bob", "alice", body, seq))
    p.expect_where(lambda r: r.get("event") == "message" and r.get("id") == items[-1][0], 5, "last message")
    got = [m["id"] for m in p.all(event="message")]
    ensure(got == [i[0] for i in items], f"message events must follow delivery order, got {got!r}")
    p.send("inbox")
    ib = p.expect(event="inbox")
    ensure([m["id"] for m in ib["messages"]] == [i[0] for i in items], f"inbox must follow arrival order: {ib!r}")


@test("C-21")
def junk_frames_ignored(ctx):
    """Frames that are not JSON objects with a type are ignored without events."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    conn.send_raw(b"this is not json\n")
    conn.send_raw(b"\n")
    conn.send_raw(b"[1, 2, 3]\n")
    conn.send_raw(b'"deliver"\n')
    conn.send_raw(b"null\n")
    conn.send_raw(b"42\n")
    conn.send_raw(b"{}\n")
    conn.send_raw(b'{"id": "x", "code": "boom"}\n')
    conn.send_raw(b'{"type": "error", "code": "trunc\n')
    conn.send_raw(b"\xff\xfe{\n")
    sid, d = sync_deliver(p, conn, "alice")
    ensure(d.get("id") == sid, f"no delivered may precede the sync message, got {d!r}")
    others = [r for r in p.records if r.get("event") not in ("started", "logged_in", "status")
              and r.get("id") != sid]
    ensure(not others, f"junk frames must produce no events, got {others!r}")
    ensure(p.running, "the client must keep running")


@test("P-5", "P-4")
def unknown_type_and_fields_ignored(ctx):
    """Unknown frame types are ignored; unknown fields on known frames are ignored."""
    peer = ctx.peer()
    p = ctx.spawn(args=client_args(peer.port, ctx.path("a")))
    p.expect(event="started", user=None)
    p.send("login alice")
    conn = handshake(peer, "alice", welcome=False)
    conn.send({"type": "welcome", "user": "alice", "protocol": 1, "motd": "hi", "features": {"x": 1}})
    p.expect(event="status", online=True)
    conn.send({"type": "reaction", "id": new_id(), "emoji": "+1"})
    conn.send({"type": "Deliver", "id": new_id(), "from": "bob", "to": "alice", "body": "x", "seq": 1})
    mid = new_id()
    msg = deliver(mid, "bob", "alice", "with extras", 5)
    msg["color"] = "red"
    msg["meta"] = {"a": [1, 2]}
    conn.send(msg)
    p.expect(event="message", id=mid, body="with extras", seq=5)
    d = conn.recv_match(type="delivered")
    ensure(d.get("id") == mid, f"the first delivered must be for the real deliver, got {d!r}")
    p.send("send bob hi")
    q = p.expect(event="queued")
    conn.recv_match(type="send")
    conn.send({"type": "ack", "id": q["id"], "seq": 6, "server_time": 123})
    p.expect(event="sent", id=q["id"], seq=6)
    ensure(len(p.all(event="message")) == 1, "unknown frame types must not create messages")
    ensure(not p.all(event="error"), "unknown frame types must not create errors")


@test("C-25")
def malformed_deliver_ignored(ctx):
    """A deliver with a missing or wrongly typed field is ignored entirely (no event, no delivered)."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    bad = []
    base = deliver(None, "bob", "alice", "bad", 1)
    for field, value in [("seq", "1"), ("seq", None), ("seq", 1.5), ("seq", [1]), ("id", 5), ("id", None),
                         ("from", 3), ("to", None), ("body", 7), ("body", ["x"])]:
        f = dict(base, id=new_id())
        f[field] = value
        bad.append(f)
    for field in ["id", "from", "to", "body", "seq"]:
        f = dict(base, id=new_id())
        del f[field]
        bad.append(f)
    for f in bad:
        conn.send(f)
    sid, d = sync_deliver(p, conn, "alice")
    ensure(d.get("id") == sid, f"no delivered may be sent for a malformed deliver, got {d!r}")
    msgs = p.all(event="message")
    ensure([m["id"] for m in msgs] == [sid], f"malformed delivers must emit nothing, got {msgs!r}")
    p.send("inbox")
    ib = p.expect(event="inbox")
    ensure([m["id"] for m in ib["messages"]] == [sid], f"inbox must hold only the valid message: {ib!r}")


@test("C-25", "C-14")
def malformed_ack_ignored(ctx):
    """An ack without a string id and integer seq is ignored; the entry stays in the outbox."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    p.send("send bob hi")
    q = p.expect(event="queued")
    conn.recv_match(type="send")
    for frame in [{"type": "ack", "id": q["id"], "seq": "1"},
                  {"type": "ack", "id": q["id"]},
                  {"type": "ack", "id": q["id"], "seq": None},
                  {"type": "ack", "id": q["id"], "seq": 2.5},
                  {"type": "ack", "id": [q["id"]], "seq": 1},
                  {"type": "ack", "seq": 1}]:
        conn.send(frame)
    sync_deliver(p, conn, "alice")
    ensure(not p.all(event="sent"), f"malformed acks must be ignored, got {p.all(event='sent')!r}")
    p.send("outbox")
    p.expect(event="outbox", messages=[{"id": q["id"], "to": "bob", "body": "hi"}])
    conn.send({"type": "ack", "id": q["id"], "seq": 3})
    p.expect(event="sent", id=q["id"], seq=3)


@test("C-24", "C-13")
def only_welcome_and_error_while_connecting(ctx):
    """Before welcome, deliver/ack frames are ignored (no event, no delivered)."""
    peer = ctx.peer()
    p = ctx.spawn(args=client_args(peer.port, ctx.path("a")))
    p.expect(event="started", user=None)
    p.send("login alice")
    conn = handshake(peer, "alice", welcome=False)
    p.send("send bob early")
    q = p.expect(event="queued")
    early = new_id()
    conn.send({"type": "ack", "id": q["id"], "seq": 1})
    conn.send(deliver(early, "bob", "alice", "too early", 1))
    conn.send({"type": "welcome", "user": "alice", "protocol": 1})
    p.expect(event="status", online=True)
    f = conn.recv_match(type="send")
    ensure(f["id"] == q["id"], "the entry must still be in the outbox after an ignored early ack")
    sid, d = sync_deliver(p, conn, "alice")
    ensure(d.get("id") == sid, f"no delivered may be sent for a pre-welcome deliver, got {d!r}")
    ensure(not p.all(event="message", id=early), "a pre-welcome deliver must not emit message")
    ensure(not p.all(event="sent"), "a pre-welcome ack must not emit sent")


@test("C-24", "C-20")
def error_handled_while_connecting(ctx):
    """An error frame received before welcome is still handled."""
    peer = ctx.peer()
    p = ctx.spawn(args=client_args(peer.port, ctx.path("a")))
    p.expect(event="started", user=None)
    p.send("login alice")
    conn = handshake(peer, "alice", welcome=False)
    conn.send({"type": "error", "code": "maintenance", "detail": "later"})
    p.expect(event="error", code="maintenance", detail="later")
    ensure(not p.all(event="status"), "no status before welcome")


@test("P-2", "P-3")
def client_frames_are_ndjson(ctx):
    """Client frames are single-line JSON objects with a string type, even for bodies with \\r and U+2028."""
    peer = ctx.peer()
    p = ctx.spawn(args=client_args(peer.port, ctx.path("a")))
    p.expect(event="started", user=None)
    p.send("login alice")
    conn = peer.accept(5)
    hello = json.loads(conn.recv_line())
    ensure(isinstance(hello, dict) and isinstance(hello.get("type"), str), f"bad hello {hello!r}")
    conn.send({"type": "welcome", "user": "alice", "protocol": 1})
    p.expect(event="status", online=True)
    body = "a\rb c d \"q\" \\ é\U0001F600"
    p.send_raw("send bob " + body + "\n")
    p.expect(event="queued", body=body)
    line = conn.recv_line()
    frame = json.loads(line)
    ensure(frame.get("type") == "send" and frame.get("body") == body, f"bad send frame {line!r}")
    ensure("\n" not in line.rstrip("\n") and "\r" not in line, f"frame must not contain raw newlines: {line!r}")
    mid = new_id()
    conn.send(deliver(mid, "bob", "alice", "x", 1))
    dline = conn.recv_line()
    dframe = json.loads(dline)
    ensure(isinstance(dframe, dict) and dframe.get("type") == "delivered" and dframe.get("id") == mid,
           f"bad delivered frame {dline!r}")


@test("P-2")
def split_and_combined_frames(ctx):
    """Frames split across TCP writes (even inside a UTF-8 sequence) and combined in one write are parsed."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    m1, m2, m3 = new_id(), new_id(), new_id()
    raw1 = (json.dumps(deliver(m1, "bob", "alice", "café \U0001F600", 1), ensure_ascii=False) + "\n").encode("utf-8")
    cut = raw1.index("\U0001F600".encode("utf-8")) + 2
    conn.send_raw(raw1[:cut])
    time.sleep(0.3)
    conn.send_raw(raw1[cut:])
    both = (json.dumps(deliver(m2, "bob", "alice", "two", 2)) + "\n" +
            json.dumps(deliver(m3, "bob", "alice", "three", 3)) + "\n").encode("utf-8")
    conn.send_raw(both)
    p.expect(event="message", id=m1, body="café \U0001F600")
    p.expect(event="message", id=m2, body="two")
    p.expect(event="message", id=m3, body="three")


@test("C-7", "C-9")
def reconnect_after_server_close(ctx):
    """When the server closes an ONLINE connection, the client goes offline and reconnects."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    conn.close()
    p.expect(event="status", online=False)
    handshake(peer, "alice")
    p.expect(event="status", online=True)
    statuses = [s["online"] for s in p.all(event="status")]
    ensure(statuses == [True, False, True], f"expected online, offline, online; got {statuses!r}")


@test("C-27", "C-7", "C-9")
def welcome_timeout(ctx):
    """Without welcome, the client closes after 5 s and retries; no status events are emitted."""
    peer = ctx.peer()
    p = ctx.spawn(args=client_args(peer.port, ctx.path("a")))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    conn = handshake(peer, "alice", welcome=False)
    t0 = time.monotonic()
    ensure(wait_closed(conn, 9), "the client must close a connection that got no welcome within 5 s")
    elapsed = time.monotonic() - t0
    ensure(elapsed >= 4.0, f"the client must wait 5 s for welcome, but closed after {elapsed:.1f} s")
    conn2 = handshake(peer, "alice", timeout=5)
    ensure(not p.all(event="status"), "leaving CONNECTING must emit nothing")
    conn2.send({"type": "welcome", "user": "alice", "protocol": 1})
    p.expect(event="status", online=True)
