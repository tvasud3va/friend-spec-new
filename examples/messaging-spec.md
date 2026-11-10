# Messaging app: specification

A command-line messaging client, built in Python and TypeScript, that talks to a message server over TCP. Rule IDs (C-1, P-3, I-2, PY-1, TS-1 ...) are unique across the whole document.

## Core: client behaviour

Everything here applies to **every** client regardless of language. Events named in `code`
are defined in `control-interface.md`.

### State model

```
            login              TCP connect + hello            welcome
 LOGGED_OUT ─────► OFFLINE ───────────────────────► CONNECTING ───────► ONLINE
                     ▲  ▲                               │                 │
                     │  └──── connect failed / closed ──┘                 │
                     └───────────────── connection lost ──────────────────┘
                                      (retry every 500 ms)

 any state ── server error `replaced` ──► STOPPED (stays offline until the process restarts)
```

The client holds three pieces of durable state in its data directory:

| item | content |
|---|---|
| `user` | the logged-in name, or none |
| `outbox` | ordered list of `{id, to, body}` not yet acked by the server |
| `inbox` | ordered list of `{id, from, to, body, seq}` received, in arrival order |

### Persistence

- **C-1** (MUST) `user`, `outbox` and `inbox` are stored durably in the data directory and survive process exit, including a kill without warning.
- **C-2** (MUST) A state change is durably written **before** the event reporting it is emitted, and before any frame that depends on it is sent (e.g. an inbox write happens before `delivered`).
- **C-3** (MUST) On start the client loads its state, then emits `started` with the stored user (or `null`). It emits no other event before `started`.
- **C-4** (MUST) On start with a stored user, the client begins connecting immediately (C-7). It never asks for the name again: login happens once per data directory.

### Login

- **C-5** (MUST) `login <name>` when no user is stored: validate the name (P-7, else error `invalid_name`), persist it, emit `logged_in`, then begin connecting.
- **C-6** (MUST) `login` when a user is already stored emits error `already_logged_in` and changes nothing.

### Connection

- **C-7** (MUST) While a user is stored and the client is not ONLINE or CONNECTING (and not STOPPED), it attempts a TCP connection. If the attempt fails, or an established connection closes for any reason, it waits 500 ms and tries again, indefinitely.
- **C-8** (MUST) After the TCP connection opens the client sends `hello {user, protocol: 1}` and becomes ONLINE only when it receives `welcome`.
- **C-27** (MUST) If `welcome` has not been received 5 seconds after the TCP connection opened, the client closes the connection and retries per C-7 (wait 500 ms, then a new attempt). This emits no event (C-9: leaving CONNECTING is silent).
- **C-9** (MUST) The client emits `status {online: true}` on entering ONLINE and `status {online: false}` on leaving ONLINE. It emits nothing for failed attempts or for leaving CONNECTING. The client starts OFFLINE without emitting a status event.
- **C-10** (MUST) On error `replaced` the client emits `error {code: "replaced", detail}` (detail per C-26), closes the connection (emitting `status {online:false}` if it was ONLINE), enters STOPPED and makes no further connection attempts until the process restarts.

### Sending (outbox-first)

There is exactly one path from the user to the server: the outbox. Online and offline sends are
handled identically; being online only means the outbox drains quickly.

- **C-11** (MUST) `send <to> <body>` validates in this order, emitting the first failing error and queuing nothing: no stored user → `not_logged_in`; `to` invalid (P-7) or equal to own name → `invalid_recipient`; body invalid (P-8) → `invalid_body`.
- **C-12** (MUST) A valid send creates a new message ID (P-9), appends `{id, to, body}` to the outbox, persists it, then emits `queued`. This is the same whether ONLINE or not.
- **C-13** (MUST) On entering ONLINE the client transmits a `send` frame for every outbox entry, in outbox order, immediately after receiving `welcome`. While ONLINE, a newly queued entry is transmitted immediately.
- **C-14** (MUST) An entry is removed from the outbox only when an `ack` with its `id` arrives: remove, persist, then emit `sent {id, seq}`.
- **C-15** (MUST) Entries not acked when a connection closes stay in the outbox, keep their `id`, and are retransmitted by C-13 on the next connection. The client never generates a new ID for a retry.
- **C-16** (MUST) An `ack` for an `id` not in the outbox is ignored.
- **C-17** (MUST) An `error` frame whose `id` matches an outbox entry removes that entry (persisted) and emits `failed {id, code}`. The entry is not retried.

### Receiving

- **C-18** (MUST) On `deliver`: if no inbox entry has that `id`, append the message to the inbox, persist, and emit `message`. Whether new or duplicate, the client then sends `delivered {id}`.
- **C-19** (MUST) A duplicate `deliver` (same `id`) never produces a second `message` event or a second inbox entry.
- **C-20** (MUST) `error` frames from the server that match no outbox entry and are not `replaced` are reported as `error {code: <server code>, detail}`.
- **C-21** (MUST) Frames that are not valid JSON objects with a `type` are ignored.
- **C-24** (MUST) While CONNECTING (after `hello`, before `welcome`) the client handles only `welcome` and `error` frames; any other frame is ignored (no event, no `delivered`).
- **C-25** (MUST) A `deliver` frame is processed only if `id`, `from`, `to` and `body` are strings and `seq` is an integer; otherwise it is ignored entirely (no event, no `delivered`). An `ack` is processed only if `id` is a string and `seq` an integer.
- **C-26** (MUST) An `error` frame is processed only if `code` is a string; otherwise it is ignored entirely (no event, no state change). When processed, `detail` is taken as `""` if it is missing or not a string, and `id` is treated as absent unless it is a string (so it matches no outbox entry, C-17). This applies to every `error` path: `replaced` (C-10), failures (C-17) and pass-through errors (C-20).

### Ordering guarantees (what observers may rely on)

- **C-22** (MUST) `queued` events appear in command order; `sent` events for messages queued by one client appear in outbox order.
- **C-23** (MUST) `message` events appear in the order the server delivered them.
- Independent streams (a `sent` vs. a `message`) have no defined relative order. Tests must not assume one.

## Core: wire protocol (version 1)

Clients talk to one server over a single TCP connection carrying newline-delimited JSON (NDJSON).
The server is the store-and-forward point: it accepts messages from senders, assigns them a global
order, and delivers them to recipients when they are connected.

Delivery is **at-least-once** in both directions, made **effectively-once** by message IDs:
senders retry until acked, receivers de-duplicate by ID.

### Framing

- **P-1** (MUST) Transport is TCP. Default server address is `127.0.0.1:7070`.
- **P-2** (MUST) Each frame is one JSON object encoded as UTF-8, followed by a single `\n` (0x0A). Frames contain no raw newlines.
- **P-3** (MUST) Every frame has a string field `type`.
- **P-4** (MUST) Receivers ignore object fields they do not recognise (forward compatibility for extensions).
- **P-5** (MUST) A client that receives a frame with an unrecognised `type` ignores it. The server answers an unrecognised `type` with `error` code `unknown_type` and keeps the connection open.
- **P-6** (MUST) A frame that is not valid JSON or lacks `type` is answered by the server with `error` code `bad_frame`; the connection stays open.

### Shared value rules

- **P-7** (MUST) A user name matches `^[A-Za-z0-9_-]{1,32}$`. Names are case-sensitive (`Alice` ≠ `alice`).
- **P-8** (MUST) A message body is a string of 1–4000 Unicode code points (not bytes, not UTF-16 units) and contains at least one non-whitespace character. Bodies are transmitted and displayed verbatim (no trimming). A code point is whitespace iff it has the Unicode White_Space property, i.e. it is exactly one of: U+0009–U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F, U+3000. Every other code point (including U+001C–U+001F, U+180E, U+200B and U+FEFF) is non-whitespace.
- **P-9** (MUST) A message ID is a lowercase RFC 4122 version-4 UUID string generated by the **sending client** when the message is created. The same ID is used for every retry of that message.
- **P-10** (MUST) `seq` is a positive integer assigned by the server when it first accepts a message. Sequence numbers are global, strictly increasing, start at 1, and are never reassigned.

### Frames

Client → server:

| type | fields | meaning |
|---|---|---|
| `hello` | `user`, `protocol` (=1) | identify this connection |
| `send` | `id`, `to`, `body` | submit a message |
| `delivered` | `id` | recipient has durably stored message `id` |

Server → client:

| type | fields | meaning |
|---|---|---|
| `welcome` | `user`, `protocol` (=1) | identification accepted |
| `ack` | `id`, `seq` | message `id` is accepted and owned by the server |
| `deliver` | `id`, `from`, `to`, `body`, `seq` | a message for this user |
| `error` | `code`, `detail`, optional `id` | something went wrong |

### Session

- **P-11** (MUST) The first frame a client sends on a new connection is `hello`. The server answers any other frame received before a successful `hello` with `error` code `not_identified`.
- **P-12** (MUST) If `hello.user` is invalid (P-7) the server sends `error` code `invalid_name` and closes the connection. If `hello.protocol` is not `1` it sends `error` code `unsupported_protocol` and closes.
- **P-13** (MUST) On a valid `hello` the server sends `welcome` and then, in ascending `seq` order, a `deliver` frame for every message pending for that user.
- **P-14** (MUST) A user has at most one live connection. A new valid `hello` for a user who is already connected makes the server send `error` code `replaced` on the **old** connection and close it.
- **P-15** (MUST) The sender's identity is the `hello.user` of the connection. The server ignores any `from` field a client puts in `send`.

### Sending

- **P-16** (MUST) On `send` the server validates `to` (P-7) and `body` (P-8). Invalid → `error` with code `invalid_recipient` or `invalid_body` and the message's `id`; the message is not stored.
- **P-17** (MUST) A message where `to` equals the sender is rejected with `invalid_recipient`.
- **P-18** (MUST) Messages to a name that has never connected are accepted and held until that user connects. There is no user registry.
- **P-19** (MUST) For a new `(sender, id)` pair the server assigns the next `seq`, stores the message as pending for the recipient, then sends `ack {id, seq}` to the sender.
- **P-20** (MUST) For an `(sender, id)` pair it has already accepted, the server sends `ack` with the **original** `seq` and does not store or deliver the message again (idempotent retry).
- **P-21** (MUST) If the recipient is connected when a message is accepted, the server sends it a `deliver` frame immediately after the sender's `ack`.
- **P-22** (MUST) The server processes frames from one connection in the order received, so messages sent in order on one connection receive increasing `seq`.

### Delivering

- **P-23** (MUST) A message stays pending for its recipient until the recipient sends `delivered` with its `id`. Pending messages that were sent on a connection that closed before `delivered` arrived are sent again (P-13) on the recipient's next connection.
- **P-24** (MUST) `delivered` for an unknown or already-delivered `id` is ignored silently.
- **P-25** (MUST) The server never delivers a message to its sender.

### Error codes

`bad_frame`, `unknown_type`, `not_identified`, `invalid_name`, `unsupported_protocol`, `replaced`,
`invalid_recipient`, `invalid_body`. Extensions may add codes; clients treat unknown codes as
generic errors (P-4 applies).

## Core: control interface (how a client process is run and driven)

Every client is a terminal program with the same arguments, stdin commands and stdout events.
This makes clients interchangeable: the same human instructions and the same conformance tests
drive any of them.

### Process

- **I-1** (MUST) The client is launched with the command given in its platform spec, plus these options in any order:
  - `--server HOST:PORT`: server address (default `127.0.0.1:7070`).
  - `--data-dir PATH`: directory for durable state (default given by platform spec). Created if missing.
  - `--json`: emit machine events instead of human text.
- **I-2** (MUST) An unknown option, a missing option value, a malformed `--server`, or any positional (non-option) word (e.g. `main.py foo`) prints a usage message to stderr and exits with code 2. Options are separate words (`--server 127.0.0.1:7070`); the `--opt=value` form is an unknown option. An option given more than once is not an error: the last occurrence wins (`--server a:1 --server b:2` uses `b:2`).
- **I-11** (MUST) `--server` is well-formed iff it is `HOST:PORT` where HOST is non-empty and contains no `:` (IPv6 literals are not supported), and PORT matches `^[0-9]+$` (ASCII digits only; no sign, no whitespace) with numeric value 1–65535. Leading zeros are allowed (`host:07070` is port 7070); `host:+80` and `host:0` are malformed.
- **I-3** (MUST) Commands are read from stdin, one per line. Lines end **only** at `\n`; one `\r` immediately before `\n` is stripped; any other `\r` is part of the line (and so of a message body). Note that common line readers also split on a lone `\r` and must not be used as-is. Each line's bytes are decoded as UTF-8 with replacement: every ill-formed sequence becomes U+FFFD and the text is processed normally; invalid UTF-8 never crashes the client or rejects the line by itself. A leading U+FEFF is kept as part of the line. End of stdin behaves like `quit`.
- **I-4** (MUST) `quit` closes the connection and exits with code 0. Durable state is already persisted (C-2), so no extra flush is required.
- **I-5** (MUST) Every output line is written to stdout and flushed immediately. Diagnostics go to stderr only.

### Commands

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

### Events (`--json` mode)

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

### Human mode (default, no `--json`)

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

## Platform: Python client (`python`)

Platform specs choose *how*, never *what*. Behaviour is defined by `core/` and `extensions/`;
if anything here seems to contradict them, core wins and the conflict is a spec bug.

- **PY-1** (MUST) Python 3.11+ standard library only. No third-party packages, no `pip install`.
- **PY-2** (MUST) Entry point is `clients/python/main.py`, launched as `python3 clients/python/main.py [options]` from the repository root. It must also work when launched from any other working directory.
- **PY-3** (MUST) Networking and timers use `asyncio` (`asyncio.open_connection`). Stdin is read on a background thread that hands lines to the event loop (portable; avoids platform-specific pipe readers). Read bytes from `sys.stdin.buffer` and split on `b"\n"` yourself, decoding each line with `line.decode("utf-8", errors="replace")` (not `"utf-8-sig"`, so a leading U+FEFF is kept): text-mode `sys.stdin` uses universal newlines and would split on a lone `\r` (I-3).
- **PY-4** (MUST) Durable state is a SQLite database `state.sqlite3` in the data directory, via the `sqlite3` module. Each state change is one committed transaction (C-2).
- **PY-5** (MUST) Default data directory: `~/.message-friend/python`.
- **PY-6** (MUST) Message IDs come from `uuid.uuid4()` formatted with `str()` (already lowercase).
- **PY-7** (MUST) Body length (P-8) is `len(body)` on a `str` (Python strings count code points). The whitespace test (P-8) uses an explicit set of the code points listed in P-8, **not** `str.isspace()` or `str.strip()` (which treat U+001C–U+001F as whitespace).
- **PY-8** (MUST) Output uses `print(..., flush=True)` or an equivalent that flushes per line; JSON is serialised with `json.dumps(obj, ensure_ascii=False)`.
- **PY-9** (SHOULD) Code is split into small modules by concern (protocol/framing, storage, client state machine, CLI), each with type hints.

## Platform: TypeScript client (`typescript`)

Platform specs choose *how*, never *what*. Behaviour is defined by `core/` and `extensions/`;
if anything here seems to contradict them, core wins and the conflict is a spec bug.

- **TS-1** (MUST) TypeScript compiled by the repository's pinned compiler (`tools/node_modules/.bin/tsc`, TypeScript 5.6) and run on Node.js 20+. Runtime code uses Node built-in modules only (`node:net`, `node:fs`, `node:readline`, `node:crypto`, …). No npm runtime dependencies; do not run `npm`.
- **TS-2** (MUST) Layout: sources in `clients/typescript/src/`, entry `src/main.ts`; compiled output in `clients/typescript/dist/` (entry `dist/main.js`). Launched as `node clients/typescript/dist/main.js [options]`; it must work from any working directory.
- **TS-3** (MUST) `clients/typescript/package.json` is exactly `{"type": "module", "private": true}` and `clients/typescript/tsconfig.json` uses: `"target": "ES2022"`, `"module": "NodeNext"`, `"moduleResolution": "NodeNext"`, `"strict": true`, `"rootDir": "src"`, `"outDir": "dist"`, `"typeRoots": ["../../tools/node_modules/@types"]`, `"types": ["node"]`, `"include": ["src"]`. Relative imports use `.js` extensions (NodeNext ESM).
- **TS-4** (MUST) The build command `tools/node_modules/.bin/tsc -p clients/typescript` succeeds with zero errors.
- **TS-5** (MUST) Networking uses `net.createConnection`; incoming bytes are buffered and split on `\n` (a frame may arrive split across or combined within `data` chunks). Stdin is read from `process.stdin` `data` events and split on `\n` yourself (do **not** use `readline`: it also splits on a lone `\r`, violating I-3). Stdin is buffered as raw bytes and split on 0x0A **before** decoding (a chunk may end mid-character); each line is decoded with `new TextDecoder("utf-8", { fatal: false, ignoreBOM: true })` (I-3).
- **TS-6** (MUST) Durable state is a JSON file `state.json` in the data directory. Every change rewrites it atomically with synchronous fs calls: write `state.json.tmp`, `fsync`, then `rename` over `state.json` (C-1, C-2).
- **TS-7** (MUST) Default data directory: `~/.message-friend/typescript`.
- **TS-8** (MUST) Message IDs come from `crypto.randomUUID()`.
- **TS-9** (MUST) Body length (P-8) is measured in code points, i.e. `[...body].length`, **not** `body.length` (which counts UTF-16 units and would disagree with other clients for emoji). The whitespace test (P-8) uses an explicit set of the code points listed in P-8, **not** `/\S/`, `\s` or `String.prototype.trim()` (which treat U+FEFF as whitespace and U+0085 as not).
- **TS-10** (MUST) Stdin commands are processed strictly one at a time in order (I-7) even though stdin data arrives asynchronously.
- **TS-11** (SHOULD) Code is split into small modules by concern (protocol/framing, storage, client state machine, CLI).

## Done means

The messaging app is done when a person using either client, in either language, can check every item below.

- [ ] **D-1** On first launch I log in with a name once; after that the client remembers me and never asks again. (rules: C-3, C-4, C-5, C-6)
- [ ] **D-2** I can send a text message to another user by name, and bad names or empty messages are refused with a clear error. (rules: C-11, C-12, P-7, P-8)
- [ ] **D-3** A message I send while offline is queued, and delivered automatically once I reconnect, even if the server once stopped answering. (rules: C-7, C-12, C-13, C-15, C-27)
- [ ] **D-4** Messages I receive are shown to me, in the order they were delivered, each exactly once. (rules: C-18, C-19, C-23)
- [ ] **D-5** Messages sent to someone who is offline reach them the next time they connect. (rules: P-18, P-13, P-23)
- [ ] **D-6** Nothing is lost if the client is killed: my name, my unsent messages and my inbox are still there on restart. (rules: C-1, C-2, C-14)
- [ ] **D-7** I can see when I am online and when I am not. (rules: C-8, C-9)
- [ ] **D-8** The assignment scenario works, with Alice and Bob on different clients: (1) Alice → Bob "Hi Bob, I have something important to tell you"; (2) Bob → Alice "What is it?"; (3) Alice goes offline and queues a message; (4) Bob goes offline and queues a message; (5) Alice reconnects and her queued message reaches the server; (6) Alice goes offline again; (7) Bob reconnects, receives Alice's message and delivers his own queued message. (rules: C-12, C-13, C-15, C-18, P-13, P-18, P-21, P-23)
- [ ] **D-9** The Python and TypeScript clients talk to each other through the same server. (rules: P-2, P-9, P-11)
- [ ] **D-10** The program can be scripted: commands on stdin, one JSON event per line on stdout, clear exit codes; typos and odd input get a clear error and never crash it. (rules: I-1, I-2, I-3, I-4, I-6, I-8, I-11, C-26)
