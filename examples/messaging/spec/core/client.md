# Core: client behaviour

Everything here applies to **every** client regardless of language. Events named in `code`
are defined in `control-interface.md`.

## State model

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

## Persistence

- **C-1** (MUST) `user`, `outbox` and `inbox` are stored durably in the data directory and survive process exit, including a kill without warning.
- **C-2** (MUST) A state change is durably written **before** the event reporting it is emitted, and before any frame that depends on it is sent (e.g. an inbox write happens before `delivered`).
- **C-3** (MUST) On start the client loads its state, then emits `started` with the stored user (or `null`). It emits no other event before `started`.
- **C-4** (MUST) On start with a stored user, the client begins connecting immediately (C-7). It never asks for the name again: login happens once per data directory.

## Login

- **C-5** (MUST) `login <name>` when no user is stored: validate the name (P-7, else error `invalid_name`), persist it, emit `logged_in`, then begin connecting.
- **C-6** (MUST) `login` when a user is already stored emits error `already_logged_in` and changes nothing.

## Connection

- **C-7** (MUST) While a user is stored and the client is not ONLINE or CONNECTING (and not STOPPED), it attempts a TCP connection. If the attempt fails, or an established connection closes for any reason, it waits 500 ms and tries again, indefinitely.
- **C-8** (MUST) After the TCP connection opens the client sends `hello {user, protocol: 1}` and becomes ONLINE only when it receives `welcome`.
- **C-27** (MUST) If `welcome` has not been received 5 seconds after the TCP connection opened, the client closes the connection and retries per C-7 (wait 500 ms, then a new attempt). This emits no event (C-9: leaving CONNECTING is silent).
- **C-9** (MUST) The client emits `status {online: true}` on entering ONLINE and `status {online: false}` on leaving ONLINE. It emits nothing for failed attempts or for leaving CONNECTING. The client starts OFFLINE without emitting a status event.
- **C-10** (MUST) On error `replaced` the client emits `error {code: "replaced", detail}` (detail per C-26), closes the connection (emitting `status {online:false}` if it was ONLINE), enters STOPPED and makes no further connection attempts until the process restarts.

## Sending (outbox-first)

There is exactly one path from the user to the server: the outbox. Online and offline sends are
handled identically; being online only means the outbox drains quickly.

- **C-11** (MUST) `send <to> <body>` validates in this order, emitting the first failing error and queuing nothing: no stored user → `not_logged_in`; `to` invalid (P-7) or equal to own name → `invalid_recipient`; body invalid (P-8) → `invalid_body`.
- **C-12** (MUST) A valid send creates a new message ID (P-9), appends `{id, to, body}` to the outbox, persists it, then emits `queued`. This is the same whether ONLINE or not.
- **C-13** (MUST) On entering ONLINE the client transmits a `send` frame for every outbox entry, in outbox order, immediately after receiving `welcome`. While ONLINE, a newly queued entry is transmitted immediately.
- **C-14** (MUST) An entry is removed from the outbox only when an `ack` with its `id` arrives: remove, persist, then emit `sent {id, seq}`.
- **C-15** (MUST) Entries not acked when a connection closes stay in the outbox, keep their `id`, and are retransmitted by C-13 on the next connection. The client never generates a new ID for a retry.
- **C-16** (MUST) An `ack` for an `id` not in the outbox is ignored.
- **C-17** (MUST) An `error` frame whose `id` matches an outbox entry removes that entry (persisted) and emits `failed {id, code}`. The entry is not retried.

## Receiving

- **C-18** (MUST) On `deliver`: if no inbox entry has that `id`, append the message to the inbox, persist, and emit `message`. Whether new or duplicate, the client then sends `delivered {id}`.
- **C-19** (MUST) A duplicate `deliver` (same `id`) never produces a second `message` event or a second inbox entry.
- **C-20** (MUST) `error` frames from the server that match no outbox entry and are not `replaced` are reported as `error {code: <server code>, detail}`.
- **C-21** (MUST) Frames that are not valid JSON objects with a `type` are ignored.
- **C-24** (MUST) While CONNECTING (after `hello`, before `welcome`) the client handles only `welcome` and `error` frames; any other frame is ignored (no event, no `delivered`).
- **C-25** (MUST) A `deliver` frame is processed only if `id`, `from`, `to` and `body` are strings and `seq` is an integer; otherwise it is ignored entirely (no event, no `delivered`). An `ack` is processed only if `id` is a string and `seq` an integer.
- **C-26** (MUST) An `error` frame is processed only if `code` is a string; otherwise it is ignored entirely (no event, no state change). When processed, `detail` is taken as `""` if it is missing or not a string, and `id` is treated as absent unless it is a string (so it matches no outbox entry, C-17). This applies to every `error` path: `replaced` (C-10), failures (C-17) and pass-through errors (C-20).

## Ordering guarantees (what observers may rely on)

- **C-22** (MUST) `queued` events appear in command order; `sent` events for messages queued by one client appear in outbox order.
- **C-23** (MUST) `message` events appear in the order the server delivered them.
- Independent streams (a `sent` vs. a `message`) have no defined relative order. Tests must not assume one.
