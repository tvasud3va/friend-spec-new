# Specification

This directory is the **source of truth** for the project. If generated code and spec disagree, the
code is wrong. Behaviour questions are answered here, never by reading generated code.

## Layout

| Path | Content | Applies to |
|---|---|---|
| `BRIEF.md` | the idea and the interview that refined it (context, not requirements) | n/a |
| `DONE.md` | **the sprint contract**: what done means, in plain language, each item tied to rules | proof (`friend verify`) |
| `core/*.md` | behaviour every implementation shares | all targets |
| `core/interface.md` | **the control interface**: how a program is launched, driven and observed. Required: it is what makes the spec testable | all targets |
| `platforms/<target>.md` | language/runtime choices only; must not change core behaviour | one target |
| `extensions/NNN-<name>.md` | additive feature layers, applied in numeric order | all targets |

A target is generated from: this README, every `core/*.md`, every `extensions/*.md` (sorted), and its own
`platforms/<target>.md`. Nothing else.

## Rule format

Every normative statement is a rule on its own bullet line:

```
- **C-12** (MUST) A queued item is removed only after it is acknowledged.
```

- IDs are `<PREFIX>-<n>`, unique across the whole spec. Each file uses its own prefix (declare it in the
  file's first paragraph). Platform prefixes: `PY`, `TS`, `JS`. Extension `NNN` uses `XNNN`.
- Levels follow RFC 2119: **MUST**, **SHOULD**, **MAY**. Prose outside rule bullets is explanation only.
- IDs are never reused. A removed rule stays with level `(REMOVED)`.
- An extension may **amend** a rule: `- **X001-3** (MUST) Amends C-12: …`. The amending rule wins.
- Every MUST should be observable through the control interface, or the tests cannot check it.

`friend spec approve` lints these rules and records the spec hash. Generation refuses an unapproved spec.

## Sprint contract (DONE.md)

`DONE.md` says what "done" means for a person, not a program. Each item is an outcome anyone can check, tied
to the rules that make it precise:

```
- [ ] **D-3** A message sent while offline is delivered once the sender reconnects. (rules: C-7, C-8)
```

It is approved with the spec. `friend verify` proves it: an item is ✓ only when every rule it cites has tests
and all of them pass on every target. The result, with the other proof checks, goes to `.friend/proof.md`.
