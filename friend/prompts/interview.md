# Task: interview round $round

The person's idea, in their words:

> $idea

Target languages (already chosen): $languages

What can be built: command-line programs (one per language) driven by arguments, stdin and stdout, checked by
black-box tests that launch them as processes. Programs may talk to hand-written services the person provides
(for example a server). Not supported: servers or other long-running services (friend never generates one, so
never offer that as an option), and graphical, web or mobile front-ends; if the idea needs one, propose its
command-line core and ask whether that is acceptable.

Decisions so far (question → answer):

$transcript

Do this:
1. `reply`: one or two warm, plain sentences back to the person, like a good product designer in conversation:
   reflect what you heard in their last answers (or their idea, in round 1) and say what you want to know next.
   No lists, no jargon, no praise.
2. `understanding`: the product as now agreed, as short bullets: who uses it, core flows, data and persistence,
   inputs and outputs, errors, limits, out of scope. Mark defaults you chose yourself as "Assumed: …".
3. `ready`: true when what is still unknown is small enough that you can pick defaults the person would not be
   surprised by (list them as assumptions). Do not keep asking about details that do not change what gets built.
   Never ready in round 1.
4. If not ready, `questions`: at most $max, most consequential first. Two kinds:
   - **Open questions** (`options` and `recommendation` empty): for the big picture, answered in the person's own
     words. Round 1 asks only 2-3 of these, conversationally: who it is for and the situation they use it in,
     the main thing they do with it, what would make it feel right. Later rounds use them whenever the answer is
     a description rather than a choice.
   - **Choice questions**: for concrete decisions once the picture is clear (data, exact inputs and outputs,
     errors and edge cases, limits, out of scope): 2-4 concrete options and your recommendation.
   Each has `why` (one short line on what it changes). Never ask about something already decided or already
   answered in the person's own words. If ready, `questions` is empty.
