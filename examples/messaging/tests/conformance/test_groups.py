"""Conformance tests for extension 001 (group chats). Each test names the spec rules it verifies. Hand-written."""
from __future__ import annotations

from lib import Ctx, check, conformance


def login(ctx: Ctx, role: str, name: str, port: int):
    c = ctx.client(role, port)
    c.wait("started", user=None)
    c.cmd(f"login {name}")
    c.wait("logged_in", user=name)
    c.wait("status", online=True)
    return c


@conformance("X001-3", "X001-5", "X001-6", "X001-8", "X001-9", "X001-11", interop=True)
def group_message_reaches_everyone_and_reply_all_works(ctx: Ctx):
    """Alice messages #house (Bob online, Carol offline); both get it marked with the group; Bob replies to all."""
    srv = ctx.server()
    alice = login(ctx, "alice", "Alice", srv.port)
    bob = login(ctx, "bob", "Bob", srv.port)

    alice.cmd("group #house Bob,Carol")
    alice.wait("group", group="#house", members=["Bob", "Carol"])
    alice.cmd("send #house dinner at 7?")
    to_bob = alice.wait("queued", to="Bob", group="#house", body="dinner at 7?")
    to_carol = alice.wait("queued", to="Carol", group="#house", body="dinner at 7?")
    check(to_bob["id"] != to_carol["id"], "each member's copy needs its own message id")

    bob.wait("group", group="#house", members=["Carol", "Alice"])           # learned from the message
    got = bob.wait("message", id=to_bob["id"])
    check(got["from"] == "Alice" and got["group"] == "#house" and got["members"] == ["Bob", "Carol", "Alice"],
          f"group message to Bob: {got}")

    carol = login(ctx, "carol", "Carol", srv.port)                          # was offline: gets it on connect
    carol.wait("message", id=to_carol["id"], group="#house")

    bob.cmd("send #house sounds good")
    to_carol2 = bob.wait("queued", to="Carol", group="#house")
    to_alice2 = bob.wait("queued", to="Alice", group="#house")
    alice.wait("message", id=to_alice2["id"], group="#house", body="sounds good", **{"from": "Bob"})
    carol.wait("message", id=to_carol2["id"], group="#house", **{"from": "Bob"})

    alice.cmd("send Bob just you")                                          # plain messages keep the core shape
    plain = alice.wait("queued", to="Bob", body="just you")
    msg = bob.wait("message", id=plain["id"])
    check("group" not in plain and "group" not in msg and "members" not in msg, f"plain message gained group fields: {msg}")


@conformance("X001-1", "X001-2", "X001-3", "X001-4", "X001-5")
def group_commands_validate_and_list(ctx: Ctx):
    """Bad group names and member lists are refused; groups are listed in creation order and persist."""
    srv = ctx.server()
    c = ctx.client("alice", srv.port)
    c.wait("started", user=None)
    c.cmd("group #house Bob"); c.wait("error", code="not_logged_in")
    c.cmd("login Alice"); c.wait("logged_in")
    c.cmd("group house Bob"); c.wait("error", code="invalid_group")
    c.cmd("group #house"); c.wait("error", code="bad_arguments")
    c.cmd("group #house Alice,Bob"); c.wait("error", code="invalid_members")      # own name
    c.cmd("group #house Bob,Bob"); c.wait("error", code="invalid_members")        # repeated
    c.cmd("group #house bad!"); c.wait("error", code="invalid_members")
    c.cmd("send #nobody hi"); c.wait("error", code="invalid_recipient")
    c.cmd("group #house Bob,Carol"); c.wait("group", group="#house")
    c.cmd("group #work Dana"); c.wait("group", group="#work")
    c.cmd("group #house Carol"); c.wait("group", group="#house", members=["Carol"])  # replaced, keeps its place
    c.cmd("groups")
    groups = c.wait("groups")["groups"]
    check(groups == [{"group": "#house", "members": ["Carol"]}, {"group": "#work", "members": ["Dana"]}], f"groups: {groups}")
    c.quit()
    c2 = ctx.client("alice", srv.port)                                            # same data dir: persisted
    c2.wait("started", user="Alice")
    c2.cmd("groups")
    check(c2.wait("groups")["groups"] == groups, "groups must survive a restart")
