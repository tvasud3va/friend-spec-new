# Repair round $round of $max_rounds for the `$target` target

The controller's checks failed. Fix the code in `$target_dir/`. Spec hash: `$spec_hash` (unchanged);
re-read the rules cited below rather than guessing from test names.

## Failing checks

$failures

Reminders: every source file starts with `$header from spec $spec_hash12. Do not edit; change the spec and regenerate.`;
keep `$target_dir/TRACE.json` current. Build (`$build`), then rerun `$test_cmd` (optionally `--only <test_name>`).
If you are confident a failing test contradicts the spec, list it in `test_disputes`.
