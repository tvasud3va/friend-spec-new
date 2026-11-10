# Task: update the conformance test suite for a spec change

Spec hash: `$spec_hash` (approved; the suite in `$tests_dir/` was written for spec `$base12`). Edit the
existing suite; do not rewrite it.

What changed in the spec:

$changes

Read the spec files in full (they are the source of truth), then:
- **Changed rules:** update every test that cites them (`$rules_cmd` shows which) so it checks the rule as it
  is now. Leave tests that do not cite a changed rule as they are, unless the change makes them wrong.
- **New rules:** add tests that cite them, following the existing files' style and helpers.
- **Removed rules:** delete tests that only check them; drop the ID from tests that check other rules too.
- Rules declared untestable before (keep, change or remove entries; return the full list in `untestable`):
$untestable

Spec files:
$spec_files

Services available to tests (started with `ctx.service(name)`):
$services

Check the whole suite as before: `$list_cmd`, `$rules_cmd`, and `$null_cmd` (every test must FAIL against the
do-nothing program). Finish with the structured result.
