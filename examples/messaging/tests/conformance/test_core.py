"""Core conformance tests. Each test names the spec rules it verifies. Hand-written."""
from __future__ import annotations

import time

from lib import Ctx, TestFailure, check, conformance

ID_FIELDS = ("id", "from", "to", "body", "seq")


def online(c, timeout=5.0):
    return c.wait("status", online=True, timeout=timeout)


def offline(c, timeout=5.0):
    return c.wait("status", online=False, timeout=timeout)


def times_out(fn) -> bool:
    try:
        fn()
        return False
    except TestFailure:
        return True


def fresh_login(ctx: Ctx, role: str, name: str, port: int):
    c = ctx.client(role, port)
    c.wait("started", user=None)
    c.cmd(f"login {name}")
    c.wait("logged_in", user=name)
    return c


# ── the assignment scenario ──────────────────────────────────────────────────

@conformance("C-12", "C-13", "C-14", "C-15", "C-18", "C-9", "P-13", "P-19", "P-21", "P-23", interop=True)
def assignment_scenario(ctx: Ctx):
    """The 7-step Alice/Bob scenario from the assignment, plus step 8 (Alice reads Bob's reply)."""
    srv = ctx.server()
    net_a, net_b = ctx.proxy(), ctx.proxy()
    alice = fresh_login(ctx, "alice", "Alice", net_a.port); online(alice)
    bob = fresh_login(ctx, "bob", "Bob", net_b.port); online(bob)

    # 1. Alice → Bob
    m1 = "Hi Bob, I have something important to tell you"
    alice.cmd(f"send Bob {m1}")
    q1 = alice.wait("queued", to="Bob", body=m1)
    alice.wait("sent", id=q1["id"])
    got = bob.wait("message", id=q1["id"])
    check(got["from"] == "Alice" and got["to"] == "Bob" and got["body"] == m1, f"step 1 message wrong: {got}")

    # 2. Bob → Alice
    bob.cmd("send Alice What is it?")
    q2 = bob.wait("queued", to="Alice", body="What is it?")
    bob.wait("sent", id=q2["id"])
    alice.wait("message", id=q2["id"], **{"from": "Bob", "body": "What is it?"})

    # 3. Alice goes offline and queues
    net_a.down(); offline(alice)
    m3 = "I'm moving to Mars"
    alice.cmd(f"send Bob {m3}")
    q3 = alice.wait("queued", body=m3)
    check([m["id"] for m in alice.query("outbox")] == [q3["id"]], "step 3: Alice's message must be in her outbox")
    alice.assert_no("sent", within=0.7, id=q3["id"])

    # 4. Bob goes offline and queues
    net_b.down(); offline(bob)
    m4 = "Wait, I can't hear you"
    bob.cmd(f"send Alice {m4}")
    q4 = bob.wait("queued", body=m4)
    check([m["id"] for m in bob.query("outbox")] == [q4["id"]], "step 4: Bob's message must be in his outbox")

    # 5. Alice reconnects; her queued message reaches the server
    net_a.up(); online(alice)
    alice.wait("sent", id=q3["id"])
    check(alice.query("outbox") == [], "step 5: Alice's outbox must be empty after ack")
    alice.assert_no("message", within=0.5, id=q4["id"])  # Bob's message is still on Bob's device

    # 6. Alice goes offline again
    net_a.down(); offline(alice)

    # 7. Bob reconnects: receives Alice's message and delivers his own
    net_b.up(); online(bob)
    got3 = bob.wait("message", id=q3["id"])
    check(got3["body"] == m3 and got3["from"] == "Alice", f"step 7: wrong message {got3}")
    bob.wait("sent", id=q4["id"])
    check(bob.query("outbox") == [], "step 7: Bob's outbox must be empty")
    inbox = bob.query("inbox")
    check([m["body"] for m in inbox] == [m1, m3], f"step 7: Bob's inbox order wrong: {inbox}")

    # 8. Alice comes back and gets Bob's reply
    net_a.up(); online(alice)
    alice.wait("message", id=q4["id"], body=m4)
    inbox = alice.query("inbox")
    check([m["body"] for m in inbox] == ["What is it?", m4], f"step 8: Alice's inbox wrong: {inbox}")
    seqs = [m["seq"] for m in bob.query("inbox")] + [m["seq"] for m in inbox]
    check(len(set(seqs)) == 4, f"each message has a unique seq: {seqs}")


# ── login & persistence ───────────────────────────────────────────────────────

@conformance("C-3", "C-4", "C-5", "C-6", "C-1", "I-8")
def login_once_per_data_dir(ctx: Ctx):
    """First launch asks for login; later launches reuse the stored name and connect immediately."""
    port = ctx.server().port
    c = ctx.client("alice", port)
    ev = c.wait("started")
    check(ev["user"] is None, f"first launch must report user null: {ev}")
    c.cmd("login alice")
    c.wait("logged_in", user="alice")
    online(c)
    check(c.events[0].get("event") == "started", f"started must be the first event: {c.events[:2]}")
    c.quit()
    c2 = ctx.client("alice", port)
    c2.wait("started", user="alice")
    online(c2)
    c2.cmd("login someone_else")
    c2.wait("error", code="already_logged_in")


@conformance("C-1", "C-2", "C-15", "C-13", "C-22", "P-22")
def offline_queue_survives_kill(ctx: Ctx):
    """Messages queued offline survive a kill -9 and flush in order on reconnect."""
    net = ctx.proxy()
    port = ctx.server().port
    a = fresh_login(ctx, "alice", "alice", net.port); online(a)
    net.down(); offline(a)
    ids = []
    for i in range(3):
        a.cmd(f"send bob message {i}")
        ids.append(a.wait("queued", body=f"message {i}")["id"])
    a.kill()
    a2 = ctx.client("alice", net.port)
    a2.wait("started", user="alice")
    check([m["id"] for m in a2.query("outbox")] == ids, "outbox must survive restart, in order")
    b = fresh_login(ctx, "bob", "bob", port); online(b)
    net.up(); online(a2)
    sent = [a2.wait("sent", id=i) for i in ids]
    check(all(sent[k]["seq"] < sent[k + 1]["seq"] for k in range(2)), f"seq must increase in outbox order: {sent}")
    got = [b.wait("message")["body"] for _ in ids]
    check(got == ["message 0", "message 1", "message 2"], f"recipient order: {got}")


@conformance("C-1", "C-18", "C-19")
def inbox_survives_restart(ctx: Ctx):
    """Received messages persist; redelivery after restart doesn't duplicate them."""
    port = ctx.server().port
    a = fresh_login(ctx, "alice", "alice", port); online(a)
    a.cmd("send bob persisted hello")
    a.wait("sent")
    b = fresh_login(ctx, "bob", "bob", port); online(b)
    b.wait("message", body="persisted hello")
    b.kill()
    b2 = ctx.client("bob", port)
    b2.wait("started", user="bob"); online(b2)
    b2.assert_no("message", within=0.5)
    check([m["body"] for m in b2.query("inbox")] == ["persisted hello"], "inbox must persist without duplicates")


@conformance("C-12", "C-14", "C-18", "I-8", "P-21", interop=True)
def online_send_event_shapes(ctx: Ctx):
    """Event fields for a normal online send match the control-interface tables exactly."""
    port = ctx.server().port
    a = fresh_login(ctx, "alice", "alice", port); online(a)
    b = fresh_login(ctx, "bob", "bob", port); online(b)
    body = "  spaces  kept   verbatim  ✓ 🎉 "
    a.cmd(f"send bob {body}")
    q = a.wait("queued")
    check(set(q) == {"event", "id", "to", "body"} and q["body"] == body and q["to"] == "bob", f"queued shape: {q}")
    check(len(q["id"]) == 36 and q["id"] == q["id"].lower() and q["id"][14] == "4", f"id must be lowercase uuid4: {q['id']}")
    s = a.wait("sent", id=q["id"])
    check(set(s) == {"event", "id", "seq"} and isinstance(s["seq"], int) and s["seq"] >= 1, f"sent shape: {s}")
    m = b.wait("message", id=q["id"])
    check(set(m) == {"event", *ID_FIELDS} and m["body"] == body and m["seq"] == s["seq"], f"message shape: {m}")
    inbox = b.query("inbox")
    check(inbox == [{k: m[k] for k in ID_FIELDS}], f"inbox entry must mirror message: {inbox}")


# ── validation & commands ─────────────────────────────────────────────────────

@conformance("C-11", "C-5", "I-6", "I-9", "P-7", "P-8", "C-12")
def validation_errors(ctx: Ctx):
    """Local validation errors, in the specified precedence, queue nothing."""
    fake = ctx.fake()  # never answers; validation is purely local
    c = ctx.client("alice", fake.port)
    c.wait("started", user=None)
    c.cmd("send bob hi"); c.wait("error", code="not_logged_in")
    c.cmd("login bad name!"); c.wait("error", code="invalid_name")
    c.cmd("login " + "x" * 33); c.wait("error", code="invalid_name")
    c.cmd("login"); c.wait("error", code="bad_arguments")
    c.cmd("login Alice"); c.wait("logged_in", user="Alice")
    c.cmd("send no/slash hi"); c.wait("error", code="invalid_recipient")
    c.cmd("send Alice hi"); c.wait("error", code="invalid_recipient")      # self
    c.cmd("send bad! "); c.wait("error", code="invalid_recipient")         # recipient checked before body
    c.cmd("send bob    "); c.wait("error", code="invalid_body")            # whitespace only
    c.cmd("send bob " + "🎉" * 4001); c.wait("error", code="invalid_body") # code points, not UTF-16
    c.cmd("send bob"); c.wait("error", code="bad_arguments")
    c.cmd("frobnicate"); c.wait("error", code="unknown_command")
    c.cmd("")
    c.cmd("outbox")
    check(c.wait("outbox")["messages"] == [], "nothing may be queued by invalid commands")
    c.cmd("send alice " + "🎉" * 4000); c.wait("queued")                   # alice ≠ Alice (case-sensitive)
    c.cmd("send bob " + "x" * 4000); c.wait("queued")


@conformance("I-2", "I-3", "I-4")
def process_lifecycle(ctx: Ctx):
    """quit and EOF exit 0; bad options exit 2."""
    port = ctx.server().port
    c = ctx.client("a", port)
    c.wait("started")
    check(c.quit() == 0, "quit must exit 0")
    c2 = ctx.client("b", port)
    c2.wait("started")
    c2.proc.stdin.close()
    check(c2.proc.wait(5) == 0, "EOF on stdin must exit 0")
    c3 = ctx.client("c", port, extra_args=["--bogus"])
    check(c3.exit_code() == 2, "unknown option must exit 2")


@conformance("I-10")
def human_mode_templates(ctx: Ctx):
    """Default (non --json) output uses the exact human templates."""
    fake = ctx.fake()
    c = ctx.client("alice", fake.port, human=True)
    c.wait_line("Welcome to message-friend. Log in with: login <your-name>")
    c.cmd("login alice"); c.wait_line("Logged in as alice")
    conn = fake.accept(); conn.welcome("alice")
    c.wait_line("* online")
    c.cmd("send bob hello  there"); c.wait_line("[queued] to bob: hello  there")
    f = conn.recv_type("send")
    conn.send({"type": "ack", "id": f["id"], "seq": 7}); c.wait_line("[sent] to bob: hello  there")
    conn.send({"type": "deliver", "id": "6f1c2a3e-1111-4222-8333-444455556666", "from": "bob", "to": "alice",
               "body": "yo", "seq": 8})
    c.wait_line("[bob] yo")
    c.cmd("inbox"); c.wait_line("[bob] yo")
    c.cmd("outbox"); c.wait_line("(outbox empty)")
    c.cmd("send carol x"); c.wait_line("[queued] to carol: x")
    f2 = conn.recv_type("send")
    conn.send({"type": "error", "code": "invalid_recipient", "detail": "no", "id": f2["id"]})
    c.wait_line("[failed:invalid_recipient] to carol: x")
    conn.send({"type": "error", "code": "x_test", "detail": "some detail"}); c.wait_line("! x_test: some detail")
    conn.send({"type": "error", "code": "x_bare", "detail": ""}); c.wait_line("! x_bare")
    conn.close()
    c.wait_line("* offline")


# ── protocol edge cases against a scripted fake server ───────────────────────

@conformance("C-8", "C-9", "P-11")
def hello_first_and_online_only_after_welcome(ctx: Ctx):
    """hello is the first frame; status online appears only after welcome."""
    fake = ctx.fake()
    c = fresh_login(ctx, "alice", "alice", fake.port)
    conn = fake.accept()
    hello = conn.recv()
    check(hello == {"type": "hello", "user": "alice", "protocol": 1}, f"hello frame: {hello}")
    c.assert_no("status", within=0.4)
    conn.send({"type": "welcome", "user": "alice", "protocol": 1})
    online(c)


@conformance("C-18", "C-19", "C-2")
def duplicate_deliver_is_deduped(ctx: Ctx):
    """Same deliver twice → one message event, delivered sent both times."""
    fake = ctx.fake()
    c = fresh_login(ctx, "bob", "bob", fake.port)
    conn = fake.accept(); conn.welcome("bob"); online(c)
    frame = {"type": "deliver", "id": "0b6f6c56-0e7e-4b0b-9d4e-2b8f2c1d3e4f", "from": "alice", "to": "bob", "body": "once", "seq": 1}
    conn.send(frame)
    check(conn.recv_type("delivered")["id"] == frame["id"], "must send delivered")
    conn.send(frame)
    check(conn.recv_type("delivered")["id"] == frame["id"], "must re-send delivered for duplicates")
    check(len(c.all("message")) == 1, f"duplicate produced extra events: {c.all('message')}")
    check(len(c.query("inbox")) == 1, "duplicate stored twice")


@conformance("C-15", "C-7", "C-14", "C-16")
def unacked_message_is_retried_with_same_id(ctx: Ctx):
    """Connection drops before ack → same id resent after reconnect (within the 500 ms retry rhythm)."""
    fake = ctx.fake()
    c = fresh_login(ctx, "alice", "alice", fake.port)
    conn = fake.accept(); conn.welcome("alice"); online(c)
    c.cmd("send bob are you there")
    first = conn.recv_type("send")
    conn.close(); offline(c)
    t0 = time.monotonic()
    conn2 = fake.accept(timeout=3); conn2.welcome("alice"); online(c)
    check(time.monotonic() - t0 < 2.5, "reconnect must be retried every 500 ms")
    again = conn2.recv_type("send")
    check(again["id"] == first["id"] and again["body"] == first["body"] and again["to"] == "bob", f"retry must reuse id: {first} vs {again}")
    conn2.send({"type": "ack", "id": "00000000-0000-4000-8000-000000000000", "seq": 99})  # unknown id: ignored
    conn2.send({"type": "ack", "id": first["id"], "seq": 5})
    c.wait("sent", id=first["id"], seq=5)
    check(len(c.all("sent")) == 1, "unknown ack must not emit sent")
    check(c.query("outbox") == [], "outbox empty after ack")


@conformance("C-17", "C-20")
def server_error_with_id_fails_message(ctx: Ctx):
    """error{id} removes the outbox entry and emits failed; other errors are reported."""
    fake = ctx.fake()
    c = fresh_login(ctx, "alice", "alice", fake.port)
    conn = fake.accept(); conn.welcome("alice"); online(c)
    c.cmd("send bob doomed")
    f = conn.recv_type("send")
    conn.send({"type": "error", "code": "invalid_recipient", "detail": "nope", "id": f["id"]})
    c.wait("failed", id=f["id"], code="invalid_recipient")
    check(c.query("outbox") == [], "failed message must leave the outbox")
    conn.send({"type": "error", "code": "something_new", "detail": "hmm"})
    c.wait("error", code="something_new", detail="hmm")
    conn.close(); offline(c)
    conn2 = fake.accept(timeout=3); conn2.welcome("alice")
    check(times_out(lambda: conn2.recv_type("send", timeout=0.8)), "failed message must not be retried")


@conformance("C-10", "P-14")
def replaced_stops_reconnecting(ctx: Ctx):
    """error replaced → error event, offline, and no further connection attempts."""
    fake = ctx.fake()
    c = fresh_login(ctx, "alice", "alice", fake.port)
    conn = fake.accept(); conn.welcome("alice"); online(c)
    conn.send({"type": "error", "code": "replaced", "detail": "newer session"})
    c.wait("error", code="replaced")
    offline(c)
    check(times_out(lambda: fake.accept(timeout=1.5)), "client reconnected after replaced")


@conformance("C-21", "P-4", "P-5", "P-2")
def tolerant_framing(ctx: Ctx):
    """Junk, unknown types, unknown fields, and split/combined TCP chunks are handled."""
    fake = ctx.fake()
    c = fresh_login(ctx, "bob", "bob", fake.port)
    conn = fake.accept(); conn.welcome("bob"); online(c)
    conn.raw(b"this is not json\n[1,2,3]\n{\"no_type\":1}\n")
    conn.send({"type": "from_the_future", "x": 1})
    d1 = '{"type":"deliver","id":"11111111-1111-4111-8111-111111111111","from":"a","to":"bob","body":"split","seq":1,"extra":true}\n'
    conn.raw(d1[:20].encode()); time.sleep(0.15); conn.raw(d1[20:].encode())
    d2 = '{"type":"deliver","id":"22222222-2222-4222-8222-222222222222","from":"a","to":"bob","body":"one","seq":2}\n'
    d3 = '{"type":"deliver","id":"33333333-3333-4333-8333-333333333333","from":"a","to":"bob","body":"two","seq":3}\n'
    conn.raw((d2 + d3).encode())
    got = [c.wait("message")["body"] for _ in range(3)]
    check(got == ["split", "one", "two"], f"frames mishandled: {got}")
    check(c.proc.poll() is None, "client must survive junk frames")


# ── rules added after the first generation surfaced spec questions ───────────

@conformance("I-12", "I-3", "C-11")
def argument_parsing_precedes_state_checks(ctx: Ctx):
    """bad_arguments beats login state; send splits at the first space; lone CR stays in the body."""
    fake = ctx.fake()
    c = ctx.client("alice", fake.port)
    c.wait("started", user=None)
    c.cmd("send bob"); c.wait("error", code="bad_arguments")         # not not_logged_in
    c.cmd("send"); c.wait("error", code="bad_arguments")
    c.cmd("login"); c.wait("error", code="bad_arguments")
    c.cmd("login a b"); c.wait("error", code="invalid_name")         # name is "a b"
    c.cmd("login alice"); c.wait("logged_in", user="alice")
    c.cmd("send bob "); c.wait("error", code="invalid_body")         # present but empty
    c.cmd("send bob a\rb"); q = c.wait("queued")
    check(q["body"] == "a\rb", f"lone CR must stay in the body (I-3): {q['body']!r}")
    c.cmd("send bob crlf\r"); q2 = c.wait("queued")
    check(q2["body"] == "crlf", f"CR before LF must be stripped (I-3): {q2['body']!r}")
    c.cmd("outbox please"); check(len(c.wait("outbox")["messages"]) == 2, "outbox ignores trailing text")


@conformance("I-2", "I-11")
def server_option_parsing(ctx: Ctx):
    """Malformed --server and --opt=value forms exit 2."""
    data = ["--data-dir", str(ctx.tmp / "x"), "--json"]
    for bad in (["--server=127.0.0.1:7070"], ["--server", "nohost"], ["--server", ":7070"], ["--server", "h:0"],
                ["--server", "h:70000"], ["--server", "[::1]:7070"], ["--server", "h:7x"], ["--server"]):
        code = ctx.client("x", 1, raw_args=data + bad).exit_code()
        check(code == 2, f"{bad} must exit 2, got {'still running' if code is None else code}")


@conformance("C-24", "C-25")
def ignores_premature_and_malformed_frames(ctx: Ctx):
    """Frames before welcome and deliver/ack frames with wrong field types are ignored entirely."""
    fake = ctx.fake()
    c = fresh_login(ctx, "bob", "bob", fake.port)
    conn = fake.accept()
    check(conn.recv().get("type") == "hello", "hello first")
    early = {"type": "deliver", "id": "44444444-4444-4444-8444-444444444444", "from": "a", "to": "bob", "body": "early", "seq": 1}
    conn.send(early)
    conn.send({"type": "welcome", "user": "bob", "protocol": 1}); online(c)
    for bad in ({"seq": "2"}, {"body": 5}, {"from": None}):
        conn.send({**early, "id": "55555555-5555-4555-8555-555555555555", "body": "bad", "seq": 2, **bad})
    good = {**early, "id": "66666666-6666-4666-8666-666666666666", "body": "good", "seq": 3}
    conn.send(good)
    check(conn.recv_type("delivered")["id"] == good["id"], "only the valid post-welcome deliver may be acknowledged")
    check([m["body"] for m in c.all("message")] == ["good"], f"ignored frames produced events: {c.all('message')}")
    c.cmd("send alice x")
    f = conn.recv_type("send")
    conn.send({"type": "ack", "id": f["id"], "seq": "7"})                    # wrong seq type: ignored
    c.assert_no("sent", within=0.4)
    conn.send({"type": "ack", "id": f["id"], "seq": 7}); c.wait("sent", id=f["id"], seq=7)
