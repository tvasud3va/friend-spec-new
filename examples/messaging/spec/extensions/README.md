# Extensions

Features beyond the core are added here as numbered files: `001-reactions.md`, `002-groups.md`, …
The generator includes every `NNN-*.md` file, in numeric order, for every target. No harness
change is needed to add one.

## Template

```markdown
# Extension 001: <name>

Depends on: core[, 00N]

## Motivation
<one paragraph: what users can now do>

## Protocol changes
- **X001-1** (MUST) New client → server frame `react {id, target_id, emoji}` …
- **X001-2** (MUST) Amends P-13: after pending `deliver` frames the server also sends …

## Client behaviour
- **X001-3** (MUST) …

## Control interface
- **X001-4** (MUST) New command `react <message-id> <emoji>` emits `reacted {…}`.
  Human template: `…`
```

## Rules for extension authors

1. New frames, fields, commands and events only. Changing existing behaviour requires an explicit
   `Amends <ID>:` rule.
2. Old clients must keep working: the core already requires unknown fields and frame types to be
   ignored (P-4, P-5), so additive frames are safe.
3. Add conformance tests under `tests/conformance/` named after the extension and tag them with
   its rule IDs. `friend verify` reports rules with no test.
4. Run `friend approve`, then `friend generate`. The harness picks the file up automatically.
