# Task: draft the specification

Read `spec/BRIEF.md` (what the user wants), `spec/README.md` (the required format) and every file in
`spec/platforms/` (the target languages; change them only as described below). Read `$kit_readme` to understand how black-box
tests will drive the programs (processes, arguments, stdin/stdout lines, exit codes, TCP).

Write `spec/core/*.md`:
- `spec/core/interface.md` (required): the **control interface**: exact command-line arguments, stdin
  commands, stdout output (prefer one JSON object per line with an `event` field, in a `--json` mode if a human
  format also exists), exit codes, error codes. Everything a test needs to drive and observe the program.
- One or more behaviour files (e.g. `spec/core/behaviour.md`, `spec/core/storage.md`): state, rules, edge cases,
  error handling, persistence. Each file declares its rule prefix and numbers its rules.

Then write `spec/DONE.md`, the **sprint contract** (format in `spec/README.md`): start with `# Done means` and
one sentence, then 5-15 items `- [ ] **D-1** <outcome>. (rules: <IDs>)`. Each item is an outcome a non-programmer
could check by using the program, tied to the rules that make it precise. Together the items should cover every
MUST behaviour a user would notice.

If the brief is itself a specification (numbered rules, exact formats), it is the source of truth: carry over
every requirement without dropping, merging or softening any, keep its rule IDs, levels and wording wherever they
fit the format, and only restructure it into the layout. Its interface section becomes the interface file (any
name in `spec/core/` is fine). Its requirements for a chosen language go into that `spec/platforms/` file, after
the template's rules; drop the template's rules only where the brief replaces them. If it relies on a component
that is not part of what gets built (for example a server), keep those rules as context and ask a question.

Scope: exactly what the brief asks for, as the smallest coherent product. Do not invent features. Where the
brief is silent but a decision is needed, choose the simplest behaviour, write it as a rule, and record it as
a question with your assumption so the user can confirm. Every MUST must be observable through the control interface.

Finish with the structured result listing the files you wrote and your questions.
