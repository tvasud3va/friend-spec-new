# Task: write the conformance test suite

Spec hash: `$spec_hash`. Write the suite in `$tests_dir/` (currently empty) using the test kit.

Read first, in full:
- `harness/testkit/README.md`: the kit's API and the rules for good tests
$spec_files

Services available to tests (hand-written components, started with `ctx.service(name)`):
$services

Requirements:
- Cover every MUST and SHOULD rule in the files above with at least one test, citing rule IDs in `@test(...)`.
  Rules you truly cannot observe through the program's external interface go in `untestable`
  with a reason (server-only rules are usually testable through a client's behaviour; keep this list short).
- Test scenarios involving several parties with `roles=(...)` and `interop=True`.
- Platform specs (`$spec_dir/platforms/`) are implementation choices: do not test them except where
  they restate core behaviour.
- Check your suite: `$list_cmd` (imports and lists tests), `$rules_cmd` (coverage map), and
  `$null_cmd` (runs it against a do-nothing program: **every test must FAIL there**).

Finish with the structured result.
