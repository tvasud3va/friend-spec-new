"""Process interface: arguments, exit codes, stdin line handling, command parsing."""
from friendkit import test

from helpers import ensure, client_args, spawn, fresh, restart, peer_client, wait_closed, send_bytes


def _expect_usage_exit(ctx, args, label):
    p = ctx.spawn(args=args)
    code = p.exit_code(timeout=5)
    ensure(code == 2, f"{label}: args {args!r} must exit with code 2, got {code!r}")


@test("I-2")
def bad_options_exit_2(ctx):
    """Unknown options, missing values, --opt=value and positional words exit with code 2."""
    data = str(ctx.path("d"))
    cases = [
        (["--data-dir", data, "--json", "--bogus"], "unknown option"),
        (["--data-dir", data, "--json", "--server"], "missing --server value"),
        (["--json", "--data-dir"], "missing --data-dir value"),
        (["--data-dir", data, "--json", "--server=127.0.0.1:7070"], "--opt=value form"),
        (["--data-dir", data, "--json", "foo"], "positional word"),
        (["foo", "--data-dir", data, "--json"], "leading positional word"),
        (["--data-dir", data, "--JSON"], "case-sensitive option name"),
    ]
    for args, label in cases:
        _expect_usage_exit(ctx, args, label)


@test("I-2", "I-11")
def malformed_server_exit_2(ctx):
    """Every malformed --server value is a usage error (exit code 2)."""
    data = str(ctx.path("d"))
    bad = [
        "127.0.0.1",
        ":7070",
        "127.0.0.1:",
        "127.0.0.1:0",
        "127.0.0.1:00000",
        "127.0.0.1:+80",
        "127.0.0.1:-1",
        "127.0.0.1:65536",
        "127.0.0.1:99999",
        "127.0.0.1: 80",
        "127.0.0.1:80 ",
        "127.0.0.1:8a",
        "127.0.0.1:0x50",
        "127.0.0.1:８０",
        "127.0.0.1:٨٠",
        "::1:7070",
        "[::1]:7070",
        "a:b:80",
    ]
    for value in bad:
        _expect_usage_exit(ctx, ["--data-dir", data, "--json", "--server", value],
                           f"malformed --server {value!r}")


@test("I-1", "I-11")
def leading_zero_port_is_valid(ctx):
    """A port with leading zeros is well-formed and denotes the same port."""
    srv = ctx.service("server")
    p = ctx.spawn(args=["--server", f"127.0.0.1:00{srv.port}", "--data-dir", str(ctx.path("a")), "--json"])
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    p.expect(event="status", online=True)


@test("I-1", "I-2")
def options_any_order_last_wins(ctx):
    """Options work in any order, and a repeated option uses its last occurrence."""
    srv = ctx.service("server")
    data = ctx.path("a")
    p = ctx.spawn(args=["--json", "--server", "127.0.0.1:1", "--data-dir", str(ctx.path("unused")),
                        "--json", "--data-dir", str(data), "--server", f"127.0.0.1:{srv.port}"])
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    p.expect(event="status", online=True)
    p.send("quit")
    ensure(p.exit_code(timeout=5) == 0, "quit must exit 0")
    # the last --data-dir was the one used
    p2 = ctx.spawn(args=client_args(srv.port, data))
    p2.expect(event="started", user="alice")


@test("I-1", "C-1")
def data_dir_created_if_missing(ctx):
    """A missing (nested) data directory is created and used for durable state."""
    srv = ctx.service("server")
    data = ctx.path("x") / "nested" / "deeper"
    p = fresh(ctx, "default", srv.port, data, "alice")
    p.send("quit")
    ensure(p.exit_code(timeout=5) == 0, "quit must exit 0")
    ensure(data.is_dir(), "data directory must be created")
    restart(ctx, "default", srv.port, data, "alice")


@test("I-3", "I-4")
def eof_behaves_like_quit(ctx):
    """End of stdin exits with code 0, like quit."""
    p = ctx.spawn(args=client_args(None, ctx.path("a"), extra=["--server", "127.0.0.1:1"]))
    p.expect(event="started", user=None)
    p.close_stdin()
    ensure(p.exit_code(timeout=5) == 0, "EOF on stdin must exit with code 0")


@test("I-4")
def quit_exits_zero_and_closes_connection(ctx):
    """quit closes the server connection and exits 0; trailing text after quit is ignored."""
    peer = ctx.peer()
    p, conn = peer_client(ctx, peer, ctx.path("a"), "alice")
    p.send("quit now please")
    ensure(p.exit_code(timeout=5) == 0, "quit must exit with code 0")
    ensure(wait_closed(conn, 5), "the connection must be closed on quit")


@test("I-3")
def crlf_and_lone_cr(ctx):
    """A \\r before \\n is stripped; any other \\r is part of the line."""
    srv = ctx.service("server")
    p = spawn(ctx, "default", srv.port, ctx.path("a"))
    p.expect(event="started", user=None)
    p.send_raw("login alice\r\n")
    p.expect(event="logged_in", user="alice")
    p.send_raw("send bob a\rb\r\n")
    p.expect(event="queued", to="bob", body="a\rb")
    p.send_raw("send bob x\r\r\n")
    p.expect(event="queued", to="bob", body="x\r")
    p.send_raw("outbox\rinbox\n")
    p.expect(event="error", code="unknown_command")
    p.send("outbox")
    ob = p.expect(event="outbox")
    ensure([m["body"] for m in ob["messages"]] == ["a\rb", "x\r"],
           f"outbox bodies must keep inner \\r, got {ob['messages']!r}")
    ensure(not p.all(event="inbox"), "'outbox\\rinbox' must not run the inbox command")


@test("I-3", "P-7")
def cr_in_name_is_part_of_it(ctx):
    """'login alice\\r\\r\\n' has name 'alice\\r', which is invalid."""
    p = ctx.spawn(args=client_args(None, ctx.path("a"), extra=["--server", "127.0.0.1:1"]))
    p.expect(event="started", user=None)
    p.send_raw("login alice\r\r\n")
    p.expect(event="error", code="invalid_name")
    ensure(not p.all(event="logged_in"), "a name ending in \\r must be rejected")


@test("I-3")
def invalid_utf8_is_replaced(ctx):
    """Ill-formed UTF-8 becomes U+FFFD and the line is processed normally."""
    p = ctx.spawn(args=client_args(None, ctx.path("a"), extra=["--server", "127.0.0.1:1"]))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    send_bytes(p, b"send bob caf\xff!\n")
    p.expect(event="queued", to="bob", body="caf�!")
    send_bytes(p, b"send bob \xff\n")
    p.expect(event="queued", to="bob", body="�")
    p.send("outbox")
    ob = p.expect(event="outbox")
    ensure(len(ob["messages"]) == 2, "both messages must be queued")


@test("I-3", "I-6")
def leading_bom_is_kept(ctx):
    """A leading U+FEFF is part of the line, so the command word is unknown."""
    p = ctx.spawn(args=client_args(None, ctx.path("a"), extra=["--server", "127.0.0.1:1"]))
    p.expect(event="started", user=None)
    p.send("﻿login alice")
    p.expect(event="error", code="unknown_command")
    p.send("login alice")
    p.expect(event="logged_in", user="alice")


@test("I-6", "I-9")
def empty_and_unknown_lines(ctx):
    """Empty lines are ignored; spaces, leading spaces, wrong case and unknown words are unknown_command."""
    p = ctx.spawn(args=client_args(None, ctx.path("a"), extra=["--server", "127.0.0.1:1"]))
    p.expect(event="started", user=None)
    p.send("")
    p.send_raw("\r\n")
    p.send("outbox")
    ob = p.expect(event="outbox")
    ensure(ob.get("messages") == [], "outbox must be empty")
    ensure(not p.all(event="error"), "empty lines must be ignored silently")
    for line in ["   ", " login alice", "LOGIN alice", "Inbox", "frobnicate", "sendx bob hi", "login\talice"]:
        p.send(line)
        p.expect(event="error", code="unknown_command")
    p.send("inbox")
    p.expect(event="inbox", messages=[])
    ensure(not p.all(event="logged_in"), "no malformed line may log in")
    ensure(len(p.all(event="error", code="unknown_command")) == 7, "each unknown line emits one error")


@test("I-12", "I-9")
def argument_errors_before_state(ctx):
    """Malformed commands emit bad_arguments even when not logged in."""
    p = ctx.spawn(args=client_args(None, ctx.path("a"), extra=["--server", "127.0.0.1:1"]))
    p.expect(event="started", user=None)
    for line in ["login", "send", "send bob", "send x.y"]:
        p.send(line)
        p.expect(event="error", code="bad_arguments")
    # present-but-empty body reaches validation: not logged in comes first
    p.send("send bob ")
    p.expect(event="error", code="not_logged_in")
    p.send("login a b")
    p.expect(event="error", code="invalid_name")
    p.send("login ")
    p.expect(event="error", code="invalid_name")
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    # still bad_arguments when logged in (parsing precedes the already_logged_in check)
    p.send("login")
    p.expect(event="error", code="bad_arguments")
    p.send("send bob")
    p.expect(event="error", code="bad_arguments")
    p.send("send bob ")
    p.expect(event="error", code="invalid_body")
    p.send("outbox")
    p.expect(event="outbox", messages=[])
    ensure(not p.all(event="queued"), "nothing may be queued")


@test("I-12", "C-11")
def send_splits_at_first_space(ctx):
    """<to> ends at the first space after 'send '; the body is verbatim, spaces included."""
    p = ctx.spawn(args=client_args(None, ctx.path("a"), extra=["--server", "127.0.0.1:1"]))
    p.expect(event="started", user=None)
    p.send("login alice")
    p.expect(event="logged_in", user="alice")
    p.send("send  bob hi")
    p.expect(event="error", code="invalid_recipient")
    p.send("send bob  hi  there  ")
    p.expect(event="queued", to="bob", body=" hi  there  ")
    p.send("send bob a b c")
    p.expect(event="queued", to="bob", body="a b c")


@test("I-12")
def trailing_text_ignored_for_inbox_outbox(ctx):
    """Text after inbox/outbox is ignored."""
    p = ctx.spawn(args=client_args(None, ctx.path("a"), extra=["--server", "127.0.0.1:1"]))
    p.expect(event="started", user=None)
    p.send("inbox please")
    p.expect(event="inbox", messages=[])
    p.send("outbox  x y")
    p.expect(event="outbox", messages=[])


@test("I-7", "I-5", "C-22")
def commands_processed_in_order(ctx):
    """A burst of commands produces their events in command order; outbox reflects them."""
    p = ctx.spawn(args=client_args(None, ctx.path("a"), extra=["--server", "127.0.0.1:1"]))
    p.expect(event="started", user=None)
    p.send_raw("login alice\nsend bob one\nsend carol two\noutbox\nsend bob three\ninbox\nfrob\noutbox\n")
    p.expect(event="logged_in", user="alice")
    q1 = p.expect(event="queued", body="one")
    q2 = p.expect(event="queued", body="two")
    ob = p.expect(event="outbox")
    ensure([m["id"] for m in ob["messages"]] == [q1["id"], q2["id"]],
           f"outbox must list the two queued messages in order, got {ob!r}")
    q3 = p.expect(event="queued", body="three")
    p.expect(event="inbox", messages=[])
    p.expect(event="error", code="unknown_command")
    ob2 = p.expect(event="outbox")
    ensure([m["id"] for m in ob2["messages"]] == [q1["id"], q2["id"], q3["id"]],
           f"outbox must list three messages in order, got {ob2!r}")
    events = [r.get("event") for r in p.records]
    ensure(events[:1] == ["started"], "started must be the first event")
