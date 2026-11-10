# Extension 001: group chats

Depends on: core. Rule prefix X001.

## Motivation

People can talk in a named group: one message to the group reaches every member, each member sees which
group it was sent in, and anyone who received a group message can reply to the whole group. A group message
is delivered as one ordinary message per member, so offline queueing, retries, ordering and de-duplication
work exactly as in the core.

## Groups

- **X001-1** (MUST) A group name is `#` followed by a name matching P-7 (e.g. `#house`). Group names are case-sensitive.
- **X001-2** (MUST) Each client keeps a durable list of groups (C-1, C-2): a group name and its members, an ordered list of 1–20 distinct user names (P-7) that never includes the logged-in user. The list is in creation order; replacing a group's members keeps its place.

## Control interface

- **X001-3** (MUST) New command `group <#name> <names>`: the text after `group ` is split at its first space into `<#name>` and `<names>`; no further space → `bad_arguments`. `<names>` is a comma-separated list (`group #house bob,carol`). Validation, first failure wins: no stored user → `not_logged_in`; `<#name>` invalid (X001-1) → `invalid_group`; `<names>` empty, more than 20 names, any name invalid (P-7) or equal to the user's own name, or a name repeated → `invalid_members`. On success the client creates the group (or replaces its members), persists it, then emits `group {group, members}`.
- **X001-4** (MUST) New command `groups` emits `groups {groups}`: an array of `{group, members}` in list order (X001-2). Any text after `groups` is ignored.
- **X001-5** (MUST) Amends C-11 and C-12: in `send <to> <body>`, a `<to>` starting with `#` is a group send. Validation, first failure wins: no stored user → `not_logged_in`; no group with that name → `invalid_recipient`; body invalid (P-8) → `invalid_body`. A valid group send appends one outbox entry per member, in member order, each with its own new message ID (P-9), `to` = that member, `group` = the group name, and `members` = the group's members followed by the sender. All entries are persisted together, then one `queued` event per entry is emitted, in member order.
- **X001-6** (MUST) Events gain fields for group messages, and only for them: `queued` adds `group`; `message` adds `group` and `members`; `inbox` and `outbox` entries add `group` and `members`. Messages that are not group messages keep exactly the core shapes (I-8).
- **X001-7** (MUST) Human mode (I-10) templates:

| event | line |
|---|---|
| `group` | `[group] {group}: {members joined by ", "}` |
| `groups` | one `[group] {group}: {members joined by ", "}` line per group, or `(no groups)` |
| `queued` (group) | `[queued] to {to} in {group}: {body}` |
| `sent` (group) | `[sent] to {to} in {group}: {body}` |
| `failed` (group) | `[failed:{code}] to {to} in {group}: {body}` |
| `message` (group) | `[{from} in {group}] {body}` |
| `inbox` line (group) | `[{from} in {group}] {body}` |
| `outbox` line (group) | `[queued] to {to} in {group}: {body}` |

New local error codes (I-9): `invalid_group`, `invalid_members`.

## Protocol

- **X001-8** (MUST) A `send` frame for a group outbox entry also carries `group` (string) and `members` (array of strings), exactly as stored in the entry.
- **X001-9** (MUST) Amends P-16 and P-19: if a `send` frame has a `group` field, the server checks that `group` is a valid group name (X001-1) and that `members` is an array of 2–21 distinct valid names (P-7) containing both the sender and `to`; otherwise it answers `error` code `invalid_group` with the message's `id` and does not store it. A valid group message keeps `group` and `members`, and its `deliver` frames carry both unchanged. Frames without `group` are handled exactly as in the core.

## Receiving

- **X001-10** (MUST) Amends C-25: a `deliver` frame with a `group` field is processed only if `group` is a valid group name (X001-1) and `members` is an array of strings that contains both `from` and the logged-in user; otherwise it is ignored entirely (no event, no `delivered`).
- **X001-11** (MUST) On a new (not duplicate, C-19) group message, the client first stores the group as `members` minus the logged-in user, in the order given, creating the group or replacing its members (X001-2); if that changed the stored list, it emits `group {group, members}`. It then stores the message (with `group` and `members`) and emits `message` as in C-18. This lets every member reply to the whole group with `send #name …`.
