# Task: update the `$target` target for a spec change

Spec hash: `$spec_hash` (approved). `$target_dir/` holds working code generated from spec `$base12`, which
passed every check then. Edit it for the change below; do not rewrite it.

What changed in the spec:

$changes

Read these spec files in full (the source of truth; the change may interact with other rules):

$spec_files

Then make the smallest coherent change: find the code for each changed or removed rule (`TRACE.json` and the
rule-ID comments point to it), update it, implement new rules where they belong in the existing structure, and
remove code that only served removed rules. Keep everything else as it is.

- Build: `$build`
- Launch: `$launch`
- Every source file starts with `$header from spec $spec_hash12. Do not edit; change the spec and regenerate.`
  (existing headers already name the new spec; new files need it too)
- Keep `$target_dir/TRACE.json` current: new rules mapped to files, removed rules dropped.
- Self-check: `$test_cmd` runs the **whole** suite (`--only <test_name>` for one test). Everything must pass,
  not only the tests for the changed rules: that is how a change that breaks something else is caught.

If you are confident a failing test contradicts the spec, list it in `test_disputes`. Return the structured result.
