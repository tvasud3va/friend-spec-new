# Task: finish generating the `$target` target (resumed)

A previous attempt was interrupted. This message is self-contained: do not rely on any earlier
conversation. Spec hash: `$spec_hash`. Target directory: `$target_dir/`.

Files currently in the target directory (possibly incomplete or broken):

$existing_files

$last_failures

Specification files (read them all; they are the only source of behaviour):

$spec_files

- Build: `$build`
- Launch: `$launch`
- Header for every source file: `$header from spec $spec_hash12. Do not edit; change the spec and regenerate.`
- Self-check: `$test_cmd`

Inspect what exists, keep what is correct, complete or fix the rest, refresh `$target_dir/TRACE.json`,
build, run the self-check, and return the structured result.
