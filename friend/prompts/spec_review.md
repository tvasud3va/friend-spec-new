# Task: review the specification for ambiguity

Read every file in `spec/` (`$kit_readme` explains how tests will drive programs). Find the **most important**
places (at most $max) where:
- two careful implementers could reasonably build observably different behaviour, or
- a test author could not decide what to assert, or
- rules contradict each other, or a MUST cannot be observed through the control interface, or
- an error/edge case that will certainly occur (bad input, restart, concurrency, limits) is unspecified.

Skip anything already decided, cosmetic wording, and implementation details the platform files leave open.
For each, give the rule IDs concerned, the question, why it matters, 2-4 concrete options, and your
recommendation, all precise enough for the spec author to apply. Order by impact.
