"""Extension 001: group chats (group/groups commands, group sends, group delivers)."""
import re

from friendkit import test

from helpers import UUID4, ensure, new_id, client_args, handshake, peer_client, deliver, sync_deliver

OFFLINE = ["--server", "127.0.0.1:1"]


def _offline(ctx, data, name=None):
    p = ctx.spawn(args=client_args(None, data, extra=OFFLINE))
    p.expect(event="started", user=None)
    if name is not None:
        p.send(f"login {name}")
        p.expect(event="logged_in", user=name)
    return p


def _gdeliver(id_, frm, to, body, seq, group, members):
    f = deliver(id_, frm, to, body, seq)
    f["group"] = group
    f["members"] = members
    return f


def _exact(r, keys, what):
    ensure(set(r) == set(keys) | {"event"}, f"{what} must have exactly fields {sorted(keys)}, got {r!r}")


@test("X001-3", "X001-4", "X001-2", "X001-1")
def group_command_creates_and_lists(ctx):
    """group creates or replaces a group and emits group{group, members}; groups lists them in creation order."""
    p = _offline(ctx, ctx.path("a"), "alice")
    p.send("groups")
    g0 = p.expect(event="groups")
    ensure(g0.get("groups") == [], f"a new client has no groups, got {g0!r}")
    _exact(g0, ["groups"], "groups event")
    p.send("group #house bob,carol")
    g = p.expect(event="group", group="#house", members=["bob", "carol"])
    _exact(g, ["group", "members"], "group event")
    p.send("group #work dave")
    p.expect(event="group", group="#work", members=["dave"])
    p.send("group #house carol,erin,Alice")
    p.expect(event="group", group="#house", members=["carol", "erin", "Alice"])
    p.send("group #House bob")
    p.expect(event="group", group="#House", members=["bob"])
    p.send("groups and more text")
    p.expect(event="groups", groups=[
        {"group": "#house", "members": ["carol", "erin", "Alice"]},
        {"group": "#work", "members": ["dave"]},
        {"group": "#House", "members": ["bob"]},
    ])


@test("X001-3", "X001-1", "X001-2")
def group_command_validation(ctx):
    """group: bad_arguments, then not_logged_in, invalid_group, invalid_members; failures change nothing."""
    p = _offline(ctx, ctx.path("a"))
    p.send("group #house")
    p.expect(event="error", code="bad_arguments")
    p.send("group house bob")
    p.expect(event="error", code="not_logged_in")
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    p.send("group #house")
    p.expect(event="error", code="bad_arguments")
    p.send("group #keep bob")
    p.expect(event="group", group="#keep", members=["bob"])
    for line in ["group house bob", "group # bob", "group ## bob", "group #a.b bob", "group #ho!me bob",
                 "group  bob", "group #" + "x" * 33 + " bob", "group house a.b", "group @keep bob"]:
        p.send(line)
        p.expect(event="error", code="invalid_group")
    many = ",".join(f"u{i}" for i in range(21))
    for line in ["group #keep ", "group #keep bob,", "group #keep ,bob", "group #keep bob,,carol",
                 "group #keep bob,alice", "group #keep alice", "group #keep bob,bob", "group #keep bob,a.b",
                 "group #keep bob, carol", "group #keep " + many, "group #keep " + "y" * 33]:
        p.send(line)
        p.expect(event="error", code="invalid_members")
    twenty = [f"u{i}" for i in range(20)]
    p.send("group #big " + ",".join(twenty))
    p.expect(event="group", group="#big", members=twenty)
    p.send("group #" + "z" * 32 + " bob")
    p.expect(event="group", group="#" + "z" * 32, members=["bob"])
    p.send("groups")
    p.expect(event="groups", groups=[{"group": "#keep", "members": ["bob"]},
                                     {"group": "#big", "members": twenty},
                                     {"group": "#" + "z" * 32, "members": ["bob"]}])
    ensure(len(p.all(event="group")) == 3, f"only valid commands may emit group: {p.all(event='group')!r}")


@test("X001-2", "C-1", "C-2")
def groups_survive_kill(ctx):
    """A kill right after the group event keeps the group list, in order, on restart."""
    data = ctx.path("a")
    p = _offline(ctx, data, "alice")
    p.send("group #b bob")
    p.expect(event="group", group="#b")
    p.send("group #a carol,dave")
    p.expect(event="group", group="#a")
    p.send("group #b erin")
    p.expect(event="group", group="#b", members=["erin"])
    p.kill()
    p2 = ctx.spawn(args=client_args(None, data, extra=OFFLINE))
    p2.expect(event="started", user="alice")
    p2.send("groups")
    p2.expect(event="groups", groups=[{"group": "#b", "members": ["erin"]},
                                      {"group": "#a", "members": ["carol", "dave"]}])


@test("X001-5", "X001-6", "X001-8", "P-9")
def group_send_queues_per_member(ctx):
    """A group send queues one entry per member, in member order, each sent with group and members."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    p.send("group #house bob,carol,dave")
    p.expect(event="group", group="#house")
    p.send("send #house hi  all")
    qs = [p.expect(event="queued", to=m, body="hi  all", group="#house") for m in ["bob", "carol", "dave"]]
    for q in qs:
        _exact(q, ["id", "to", "body", "group"], "group queued event")
        ensure(UUID4.match(q["id"]), f"id must be a lowercase UUIDv4: {q['id']!r}")
    ensure(len({q["id"] for q in qs}) == 3, f"each entry needs its own id: {qs!r}")
    members = ["bob", "carol", "dave", "alice"]
    frames = [conn.recv_match(type="send") for _ in range(3)]
    for q, f in zip(qs, frames):
        ensure(f.get("id") == q["id"] and f.get("to") == q["to"] and f.get("body") == "hi  all",
               f"send frames must follow member order, got {f!r} for {q!r}")
        ensure(f.get("group") == "#house" and f.get("members") == members,
               f"a group send frame must carry group and members {members!r}, got {f!r}")
    p.send("outbox")
    ob = p.expect(event="outbox")
    ensure(ob["messages"] == [{"id": q["id"], "to": q["to"], "body": "hi  all", "group": "#house",
                               "members": members} for q in qs],
           f"outbox entries must carry group and members, got {ob!r}")
    p.send("send bob plain")
    q4 = p.expect(event="queued", to="bob", body="plain")
    _exact(q4, ["id", "to", "body"], "non-group queued event")
    f4 = conn.recv_match(type="send")
    ensure(f4.get("id") == q4["id"], f"expected the plain send frame, got {f4!r}")
    for i, q in enumerate(qs):
        conn.send({"type": "ack", "id": q["id"], "seq": i + 1})
    p.expect_where(lambda r: r.get("event") == "sent" and r.get("id") == qs[2]["id"], 5, "sent for dave")
    p.send("outbox")
    ob2 = p.expect(event="outbox")
    ensure(ob2["messages"] == [{"id": q4["id"], "to": "bob", "body": "plain"}],
           f"a non-group outbox entry keeps the core shape, got {ob2!r}")


@test("X001-5")
def group_send_validation(ctx):
    """send #g: not_logged_in, then invalid_recipient for an unknown group, then invalid_body."""
    p = _offline(ctx, ctx.path("a"))
    p.send("send #house hi")
    p.expect(event="error", code="not_logged_in")
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    p.send("send #house hi")
    p.expect(event="error", code="invalid_recipient")
    p.send("send #house ")
    p.expect(event="error", code="invalid_recipient")
    p.send("group #house bob,carol")
    p.expect(event="group", group="#house")
    p.send("send #House hi")
    p.expect(event="error", code="invalid_recipient")
    p.send("send #house ")
    p.expect(event="error", code="invalid_body")
    p.send("send #house    ")
    p.expect(event="error", code="invalid_body")
    p.send("outbox")
    p.expect(event="outbox", messages=[])
    ensure(not p.all(event="queued"), "nothing may be queued")


@test("X001-5", "X001-8", "C-1", "C-2", "C-13")
def group_send_offline_survives_kill(ctx):
    """Group entries queued offline survive a kill and are sent with group and members after welcome."""
    peer = ctx.peer()
    data = ctx.path("a")
    p = _offline(ctx, data, "alice")
    p.send("group #trip carol,bob")
    p.expect(event="group", group="#trip")
    p.send("send #trip see you")
    qs = [p.expect(event="queued", to=m, group="#trip") for m in ["carol", "bob"]]
    p.kill()
    p2 = ctx.spawn(args=client_args(peer.port, data))
    p2.expect(event="started", user="alice")
    conn = handshake(peer, "alice")
    p2.expect(event="status", online=True)
    frames = [conn.recv_match(type="send") for _ in range(2)]
    ensure([f.get("id") for f in frames] == [q["id"] for q in qs], f"stored entries must be sent in order: {frames!r}")
    for f in frames:
        ensure(f.get("group") == "#trip" and f.get("members") == ["carol", "bob", "alice"] and
               f.get("body") == "see you", f"bad group send frame after restart: {f!r}")


@test("X001-11", "X001-6", "X001-10", "X001-5", "X001-2")
def group_message_received(ctx):
    """A new group message stores the group (members minus self), emits group then message, and allows replies."""
    peer = ctx.peer()
    data = ctx.path("a")
    p, conn = peer_client(ctx, peer, data, "alice")
    m1 = new_id()
    conn.send(_gdeliver(m1, "bob", "alice", "hello group", 3, "#trip", ["bob", "alice", "carol"]))
    g = p.expect(event="group", group="#trip", members=["bob", "carol"])
    _exact(g, ["group", "members"], "group event")
    msg = p.expect(event="message", id=m1)
    _exact(msg, ["id", "from", "to", "body", "seq", "group", "members"], "group message event")
    ensure(msg["from"] == "bob" and msg["to"] == "alice" and msg["body"] == "hello group" and msg["seq"] == 3
           and msg["group"] == "#trip" and msg["members"] == ["bob", "alice", "carol"], f"bad message {msg!r}")
    d = conn.recv_match(type="delivered")
    ensure(d.get("id") == m1, f"expected delivered for the group message, got {d!r}")
    # same membership: no group event
    m2 = new_id()
    conn.send(_gdeliver(m2, "carol", "alice", "me too", 4, "#trip", ["bob", "alice", "carol"]))
    p.expect(event="message", id=m2, group="#trip")
    # changed membership: group event with the new list
    m3 = new_id()
    conn.send(_gdeliver(m3, "carol", "alice", "dave joins", 5, "#trip", ["carol", "dave", "alice", "bob"]))
    p.expect(event="group", group="#trip", members=["carol", "dave", "bob"])
    p.expect(event="message", id=m3)
    # a duplicate never changes the group
    conn.send(_gdeliver(m1, "bob", "alice", "hello group", 3, "#trip", ["bob", "alice"]))
    sync_deliver(p, conn, "alice")
    groups_ev = p.all(event="group")
    ensure([e["members"] for e in groups_ev] == [["bob", "carol"], ["carol", "dave", "bob"]],
           f"group events only on new messages that change the list, got {groups_ev!r}")
    ensure(len(p.all(event="message", id=m1)) == 1, "a duplicate must not produce a second message")
    p.send("groups")
    p.expect(event="groups", groups=[{"group": "#trip", "members": ["carol", "dave", "bob"]}])
    p.send("inbox")
    ib = p.expect(event="inbox")
    ensure(ib["messages"][:3] == [
        {"id": m1, "from": "bob", "to": "alice", "body": "hello group", "seq": 3, "group": "#trip",
         "members": ["bob", "alice", "carol"]},
        {"id": m2, "from": "carol", "to": "alice", "body": "me too", "seq": 4, "group": "#trip",
         "members": ["bob", "alice", "carol"]},
        {"id": m3, "from": "carol", "to": "alice", "body": "dave joins", "seq": 5, "group": "#trip",
         "members": ["carol", "dave", "alice", "bob"]},
    ], f"inbox entries must carry group and members, got {ib!r}")
    ensure(set(ib["messages"][3]) == {"id", "from", "to", "body", "seq"},
           f"a non-group inbox entry keeps the core shape, got {ib['messages'][3]!r}")
    p.send("send #trip back")
    qs = [p.expect(event="queued", to=m, group="#trip") for m in ["carol", "dave", "bob"]]
    frames = [conn.recv_match(type="send") for _ in range(3)]
    ensure([f.get("id") for f in frames] == [q["id"] for q in qs], f"reply frames in member order: {frames!r}")
    ensure(all(f.get("members") == ["carol", "dave", "bob", "alice"] for f in frames),
           f"reply members are the group's members followed by the sender: {frames!r}")


@test("X001-11", "X001-2")
def group_message_updates_local_group(ctx):
    """A received group message replaces a locally created group's members only if they differ."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    p.send("group #a bob,carol")
    p.expect(event="group", group="#a")
    p.send("group #b dave")
    p.expect(event="group", group="#b")
    m1 = new_id()
    conn.send(_gdeliver(m1, "bob", "alice", "same", 1, "#a", ["bob", "alice", "carol"]))
    p.expect(event="message", id=m1)
    ensure(len(p.all(event="group")) == 2, f"an unchanged group must not emit group: {p.all(event='group')!r}")
    m2 = new_id()
    conn.send(_gdeliver(m2, "carol", "alice", "reordered", 2, "#a", ["carol", "bob", "alice"]))
    p.expect(event="group", group="#a", members=["carol", "bob"])
    p.expect(event="message", id=m2)
    p.send("groups")
    p.expect(event="groups", groups=[{"group": "#a", "members": ["carol", "bob"]},
                                     {"group": "#b", "members": ["dave"]}])


@test("X001-10")
def malformed_group_deliver_ignored(ctx):
    """A group deliver with a bad group name or members lacking from/self is ignored entirely."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    cases = [("house", ["bob", "alice"]), ("#", ["bob", "alice"]), ("#a.b", ["bob", "alice"]),
             ("##x", ["bob", "alice"]), (5, ["bob", "alice"]), (["#x"], ["bob", "alice"]),
             ("#x", ["alice", "carol"]), ("#x", ["bob", "carol"]), ("#x", ["bob"]), ("#x", []),
             ("#x", "bob,alice"), ("#x", None), ("#x", ["bob", "alice", 3]), ("#x", ["Bob", "alice"])]
    for group, members in cases:
        conn.send(_gdeliver(new_id(), "bob", "alice", "bad", 1, group, members))
    f = deliver(new_id(), "bob", "alice", "no members", 1)
    f["group"] = "#x"
    conn.send(f)
    sid, d = sync_deliver(p, conn, "alice")
    ensure(d.get("id") == sid, f"no delivered may be sent for an invalid group deliver, got {d!r}")
    msgs = p.all(event="message")
    ensure([m["id"] for m in msgs] == [sid], f"invalid group delivers must emit nothing, got {msgs!r}")
    ensure(not p.all(event="group"), f"invalid group delivers must not create groups: {p.all(event='group')!r}")
    ok = new_id()
    conn.send(_gdeliver(ok, "bob", "alice", "fine", 2, "#x", ["alice", "bob"]))
    p.expect(event="group", group="#x", members=["bob"])
    p.expect(event="message", id=ok, group="#x")
    p.send("groups")
    p.expect(event="groups", groups=[{"group": "#x", "members": ["bob"]}])


@test("X001-11", "X001-2", "C-1", "C-2")
def received_group_survives_kill(ctx):
    """A kill right after a group message keeps the group and the inbox entry with group and members."""
    peer = ctx.peer()
    data = ctx.path("a")
    p, conn = peer_client(ctx, peer, data, "alice")
    mid = new_id()
    conn.send(_gdeliver(mid, "bob", "alice", "keep", 7, "#k", ["alice", "bob", "carol"]))
    p.expect(event="message", id=mid)
    p.kill()
    p2 = ctx.spawn(args=client_args(None, data, extra=OFFLINE))
    p2.expect(event="started", user="alice")
    p2.send("groups")
    p2.expect(event="groups", groups=[{"group": "#k", "members": ["bob", "carol"]}])
    p2.send("inbox")
    p2.expect(event="inbox", messages=[{"id": mid, "from": "bob", "to": "alice", "body": "keep", "seq": 7,
                                        "group": "#k", "members": ["alice", "bob", "carol"]}])


def _human(ctx, port, data):
    return ctx.spawn(args=client_args(port, data, json_mode=False), json_lines=False)


@test("X001-7")
def group_human_mode(ctx):
    """Human mode prints the group, groups, queued, outbox, sent, failed, message and inbox group lines."""
    peer = ctx.peer()
    p = _human(ctx, peer.port, ctx.path("a"))
    p.send("login alice")
    p.expect_line("Logged in as alice")
    conn = handshake(peer, "alice")
    p.expect_line("* online")
    p.send("groups")
    p.expect_line("(no groups)")
    p.send("group #house bob,carol")
    p.expect_line("[group] #house: bob, carol")
    p.send("send #house hi  there")
    p.expect_line("[queued] to bob in #house: hi  there")
    p.expect_line("[queued] to carol in #house: hi  there")
    f1 = conn.recv_match(type="send")
    f2 = conn.recv_match(type="send")
    p.send("outbox")
    p.expect_line("[queued] to bob in #house: hi  there")
    p.expect_line("[queued] to carol in #house: hi  there")
    conn.send({"type": "ack", "id": f1["id"], "seq": 1})
    p.expect_line("[sent] to bob in #house: hi  there")
    conn.send({"type": "error", "code": "invalid_group", "detail": "", "id": f2["id"]})
    p.expect_line("[failed:invalid_group] to carol in #house: hi  there")
    conn.send(_gdeliver(new_id(), "dave", "alice", "yo  all", 2, "#trip", ["dave", "alice", "erin"]))
    p.expect_line("[group] #trip: dave, erin")
    p.expect_line("[dave in #trip] yo  all")
    p.send("inbox")
    p.expect_line("[dave in #trip] yo  all")
    p.send("groups")
    p.expect_line("[group] #house: bob, carol")
    p.expect_line("[group] #trip: dave, erin")
    ensure(not any(re.match(r"\s*\{", l) for l in p.lines), "human mode must not print JSON")
