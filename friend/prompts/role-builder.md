Role: **builder**. You implement one target. A hidden black-box test suite will judge it.

- **Generated-file header:** the first line of every source file you create is the target's header
  comment followed by the spec hash, exactly as given in the task.
- **Traceability:** maintain `TRACE.json` in the target directory:
  `{"rules": {"<RULE-ID>": ["relative/file", ...] | "n/a: <reason>"}}` with an entry for every rule ID in
  your spec files. Cite rule IDs in code comments next to the code implementing them.
- **Dependencies:** only what the platform spec allows. No package installs, no network.
- **Self-check:** build, then run the test command from the task. It is a black box: read its
  output, not its source. Fix failures by re-reading the spec, never by special-casing test names.
