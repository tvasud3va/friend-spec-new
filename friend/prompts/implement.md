# Task: generate the `$target` target

Spec hash: `$spec_hash` (approved). Target directory: `$target_dir/` (currently empty).

Read these spec files in full, in this order, before writing any code. Together they are the complete
specification for this target:

$spec_files

Run configuration:
- Build (run by the controller before every check; must succeed): `$build`
- Launch: `$launch`
- Header for every source file: `$header from spec $spec_hash12. Do not edit; change the spec and regenerate.`
- Source files are matched by: $sources
- Self-check (black box): `$test_cmd` (add `--only <test_name>` for one test)

Work plan: read all spec files and list every rule ID → design the module layout the platform spec asks
for → implement, citing rule IDs → write `$target_dir/TRACE.json` → build and run the self-check, fixing
failures until it passes → return the structured result.
