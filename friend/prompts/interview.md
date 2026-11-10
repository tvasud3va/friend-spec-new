# Task: interview round $round

The person's idea, in their words:

> $idea

Target languages (already chosen): $languages

What can be built: command-line programs (one per language) driven by arguments, stdin and stdout, checked by
black-box tests that launch them as processes. Programs may talk to hand-written services the person provides
(for example a server). Not supported: graphical, web or mobile front-ends; if the idea needs one, propose its
command-line core and ask whether that is acceptable.

Decisions so far (question → answer):

$transcript

Do this:
1. `understanding`: the product as now agreed, as short bullets: who uses it, core flows, data and persistence,
   inputs and outputs, errors, limits, out of scope. Mark defaults you chose yourself as "Assumed: …".
2. `ready`: true when what is still unknown is small enough that you can pick defaults the person would not be
   surprised by (list them as assumptions). Do not keep asking about details that do not change what gets built.
3. If not ready, `questions`: at most $max, the most consequential first. Each has `why` (one short line on what
   it changes), 2-4 concrete `options` in plain language, and your `recommendation`. Never ask about something
   already decided. If ready, `questions` is empty.
