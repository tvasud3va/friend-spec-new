# Task: arbitrate test disputes

Spec hash `$spec_hash`. Spec files: $spec_files
The test suite is in `$tests_dir/`.

The implementer of target `$target` disputes these failing tests:

$disputes

For each disputed test: read the test's source and the cited rules, then decide:
- `test_wrong`: the test asserts something the spec does not require, or contradicts it. Give `fix`:
  exactly what the test should assert instead (or that it should be removed).
- `code_wrong`: the test correctly reflects the spec; the implementation must change. Explain which rule text.
- `spec_ambiguous`: the spec genuinely allows both readings. Give `fix`: the question the spec must answer.
