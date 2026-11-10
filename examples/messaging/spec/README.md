# message-friend specification

This directory is the **source of truth**. If code and spec disagree, the code is wrong.
Behaviour questions are answered here, never by reading generated code.

## Layout

| Path | Scope | Applies to |
|---|---|---|
| `core/protocol.md` | Wire protocol between any client and the server | all clients, server |
| `core/client.md` | Client state, offline queue, reconnect, persistence | all clients |
| `core/control-interface.md` | Process interface (CLI args, stdin commands, stdout events) | all clients |
| `platforms/<target>.md` | Language/runtime choices only. Must not change core behaviour | one client |
| `extensions/NNN-<name>.md` | Additive feature layers (reactions, groups, …), applied in numeric order | all clients |

A generator target receives: every `core/*.md`, every `extensions/*.md` (sorted), and its own
`platforms/<target>.md`. Nothing else.

## Rule format

Every normative statement is a rule on its own bullet line:

```
- **C-12** (MUST) A queued message is removed from the outbox only after the server acks it.
```

- IDs are `<PREFIX>-<n>`, unique across the whole spec. Prefixes: `P` protocol, `C` client,
  `I` control interface, `PY`/`TS` platforms, `X<nnn>` extension number `nnn`.
- Levels follow RFC 2119: **MUST**, **SHOULD**, **MAY**.
- Prose outside rule bullets is explanation, not requirement.
- IDs are never reused. A removed rule is left with level `(REMOVED)` so traces stay meaningful.
- An extension may **amend** a core rule by writing `- **X001-3** (MUST) Amends C-12: …`.
  The amending rule wins.

`friend approve` lints these rules (unique IDs, valid levels, amended IDs exist) and records the spec
hash in `spec.lock.json`. Generation refuses to run against an unapproved spec.
