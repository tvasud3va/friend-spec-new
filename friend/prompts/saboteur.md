# Task: create mutants of the `$target` target

Spec hash `$spec_hash`. Spec files: $spec_files

Each directory below is a full copy of a working, passing implementation. In each, make the smallest
code change that makes the program violate **only** the assigned rule (observable through its external
interface), keeping it building and otherwise working. Do not add comments explaining the change.

$assignments

Return one entry per mutant directory describing the change.
