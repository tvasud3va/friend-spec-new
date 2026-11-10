# Extensions

Add features as numbered files: `001-<name>.md`, `002-<name>.md`, … Each is included for every target,
in numeric order, with no generator change. New behaviour only; changing existing behaviour requires an
explicit `Amends <ID>:` rule. Use rule prefix `X<NNN>`. Then `friend spec approve` and `friend generate`.
