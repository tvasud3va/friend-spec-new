# Core: control interface (how a client process is run and driven)

Every client is a terminal program with the same arguments, stdin commands and stdout events.
This makes clients interchangeable: the same human instructions and the same conformance tests
drive any of them.

## Process

- **I-1** (MUST) The client is launched with the command given in its platform spec, plus these options in any order:
  - `--server HOST:PORT`: server address (default `127.0.0.1:7070`).
  - `--data-dir PATH`: directory for durable state (default given by platform spec). Created if missing.
  - `--json`: emit machine events instead of human text.
- **I-2** (MUST) An unknown option, a missing option value, a malformed `--server`, or any positional (non-option) word (e.g. `main.py foo`) prints a usage message to stderr and exits with code 2. Options are separate words (`--server 127.0.0.1:7070`); the `--opt=value` form is an unknown option. An option given more than once is not an error: the last occurrence wins (`--server a:1 --server b:2` uses `b:2`).
- **I-11** (MUST) `--server` is well-formed iff it is `HOST:PORT` where HOST is non-empty and contains no `:` (IPv6 literals are not supported), and PORT matches `^[0-9]+$` (ASCII digits only; no sign, no whitespace) with numeric value 1–65535. Leading zeros are allowed (`host:07070` is port 7070); `host:+80` and `host:0` are malformed.
- **I-3** (MUST) Commands are read from stdin, one per line. Lines end **only** at `\n`; one `\r` immediately before `\n` is stripped; any other `\r` is part of the line (and so of a message body). Note that common line readers also split on a lone `\r` and must not be used as-is. Each line's bytes are decoded as UTF-8 with replacement: every ill-formed sequence becomes U+FFFD and the text is processed normally; invalid UTF-8 never crashes the client or rejects the line by itself. A leading U+FEFF is kept as part of the line. End of stdin behaves like `quit`.
- **I-4** (MUST) `quit` closes the connection and exits with code 0. Durable state is already persisted (C-2), so no extra flush is required.
- **I-5** (MUST) Every output line is written to stdout and flushed immediately. Diagnostics go to stderr only.

## Commands

The command word is the text up to the first space. Arguments are separated by exactly one space.

| command | syntax | notes |
|---|---|---|
| `login` | `login <name>` | C-5, C-6 |
| `send` | `send <to> <body>` | `<body>` is **everything after the second space**, verbatim, including further spaces |
| `inbox` | `inbox` | emits `inbox` |
| `outbox` | `outbox` | emits `outbox` |
| `quit` | `quit` | I-4 |

- **I-6** (MUST) A line that is empty (zero length after the I-3 `\r` stripping) is ignored. Every other line is parsed literally, with no trimming: a line of only spaces or with leading spaces (`   `, ` login alice`) has an empty or unknown command word. Command words are case-sensitive (`LOGIN alice`). An unknown command word emits error `unknown_command`.
- **I-12** (MUST) Argument parsing happens before any state check (login state, validation), so a malformed command always emits `bad_arguments`, even when not logged in:
  - `login`: the line must be `login` followed by a space; everything after that first space is `<name>` (so `login a b` has name `a b` → `invalid_name`). `login` alone → `bad_arguments`.
  - `send`: the text after `send ` is split at its **first** space into `<to>` and `<body>`. No `send ` prefix or no further space → `bad_arguments`. An empty `<body>` (`send bob `) is present-but-empty and goes to validation (`invalid_body`).
  - `inbox`, `outbox`, `quit`: any text after the command word is ignored.
- **I-13** (MAY) The `detail` of locally generated errors is free-form help text (may be empty). Observers must not depend on it.
- **I-7** (MUST) Commands are processed in the order read. A command's own events are emitted before the next command is processed. `inbox`/`outbox` reflect all previously processed commands and all frames already handled.

## Events (`--json` mode)

- **I-8** (MUST) In `--json` mode each stdout line is exactly one JSON object with a string field `event`, using exactly these shapes (field order is not significant; extensions may add fields):

| event | fields | emitted by |
|---|---|---|
| `started` | `user` (string or `null`) | C-3 |
| `logged_in` | `user` | C-5 |
| `status` | `online` (bool) | C-9 |
| `queued` | `id`, `to`, `body` | C-12 |
| `sent` | `id`, `seq` | C-14 |
| `failed` | `id`, `code` | C-17 |
| `message` | `id`, `from`, `to`, `body`, `seq` | C-18 |
| `inbox` | `messages`: array of `{id, from, to, body, seq}` in inbox order | `inbox` |
| `outbox` | `messages`: array of `{id, to, body}` in outbox order | `outbox` |
| `error` | `code`, `detail` (string, may be empty) | various |

- **I-9** (MUST) Local error codes: `not_logged_in`, `already_logged_in`, `invalid_name`, `invalid_recipient`, `invalid_body`, `unknown_command`, `bad_arguments`. Server error codes are passed through (C-20).

## Human mode (default, no `--json`)

- **I-10** (MUST) Without `--json`, each event is printed as one line using these templates (`{x}` is substituted verbatim):

| event | line |
|---|---|
| `started` (no user) | `Welcome to message-friend. Log in with: login <your-name>` |
| `started` (user) | `Logged in as {user}` |
| `logged_in` | `Logged in as {user}` |
| `status` | `* online` or `* offline` |
| `queued` | `[queued] to {to}: {body}` |
| `sent` | `[sent] to {to}: {body}` |
| `failed` | `[failed:{code}] to {to}: {body}` |
| `message` | `[{from}] {body}` |
| `inbox` | one `[{from}] {body}` line per message, or `(inbox empty)` |
| `outbox` | one `[queued] to {to}: {body}` line per message, or `(outbox empty)` |
| `error` | `! {code}: {detail}`, or `! {code}` if detail is empty |

For `sent` and `failed`, the client still has `to` and `body` from the outbox entry being removed.
