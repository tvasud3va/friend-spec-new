# friend

**Describe an app. Get interviewed. Approve a spec and a definition of done. Get working code, plus proof
that it's done.**

friend is a command-line tool that turns an idea into tested programs by driving
[Claude Code](https://claude.com/claude-code). Claude does the writing. friend decides what each agent may
see and touch, and checks every result with ordinary code that no agent can talk its way past.

```
$ friend
What do you want to build? a habit tracker I use from the terminal: add habits, check them off, see streaks
Folder name for it (press Enter to use the suggestion) [habit-tracker-terminal]: habits
Languages [1]: 1      (1 python · 2 typescript · 3 javascript)

I1. How often is each habit meant to be done?
    1) Every day for every habit
    2) Each habit picks its own schedule
    recommended: Every day for every habit. It's the simplest and matches 'check them off each day'.
    > 1
...
approve the spec and contract? [y]es · [a]sk me more · [e]dit it myself > y
build it now? this takes a while (20-40 min) [Y/n] >        # plan → tests → code → audit, then proof of done

$ cd habits && friend run python list
```

## Use it on your own computer

### 1. What you need

| | Why | Check |
|---|---|---|
| **Python 3.11+** | friend itself (no other Python packages) | `python3 --version` |
| **Claude Code**, logged in | writes the specs, tests and code | `claude --version`, then `claude auth login` |
| **Node.js** | only for TypeScript or JavaScript programs, and the messaging example | `node --version` |
| **git** | to download friend | `git --version` |

A Claude subscription works. friend reports agent costs in API-equivalent dollars, so you can see what a
run would cost on the API.

### 2. Install

```sh
git clone https://github.com/tvasud3va/friend-spec.git
cd friend-spec
uv tool install .            # or: pipx install .   or: python3 -m pip install --user .
friend --help                # confirms it installed
```

Inside any friend project (including the example below), `friend doctor` checks Claude Code, your login and
toolchains.

After the install, `friend` works from any folder. To update later, run `git pull` in `friend-spec`
followed by `uv tool install --force --reinstall .`

### 3. Build something of your own

```sh
cd ~                         # anywhere outside the friend-spec folder
friend                       # describe your idea; friend makes a folder for it, interviews you, then builds
```

Already have a written spec? Skip the interview:

```sh
friend new --from my-spec.md
```

When it finishes, friend prints how to run your program (`friend run <language> <arguments>`), and
`friend verify` shows the proof that each item of your definition of done works.

A first build usually takes 20–40 minutes. Press Ctrl-C at any time and run `friend generate` later to
pick up where it stopped.

### 4. Try the messaging example (no AI needed)

The repo includes a finished messaging app: a small server plus Python and TypeScript clients that friend
generated from `examples/messaging/spec/`. You can check it and use it without running any agents.

```sh
cd friend-spec/examples/messaging
(cd tools && npm ci)         # pinned TypeScript compiler
friend doctor                # everything should show ✓
friend verify                # builds both clients and runs every check (a few minutes, no API cost)
python3 demo.py              # watch Alice (Python) and Bob (TypeScript) go through the offline scenario
sh chat.sh                   # macOS: opens two Terminal windows to chat yourself (login Alice / login Bob)
```

On a fresh copy, `friend verify` passes everything except the mutation audit line: that result is kept in
local run records (`.friend/`, not in git) and only `friend generate` produces it. To rebuild the whole
example from its spec yourself (about an hour):

```sh
friend clean --all && friend approve spec && friend generate --yes && friend verify
```

## How it works

```
idea ─► interview ─► spec + DONE.md ─► approve ─► plan ─► tests ─► code ─► interop ─► audit ─► proof
        (friend new)                  (you)      └──────────── friend generate ───────────┘   (friend verify)
```

1. **Interview** (`friend new`). An interviewer agent asks a few open, plain-language questions, then
   choices with a recommended answer. It stops once the remaining unknowns can be settled by defaults you
   wouldn't be surprised by.
2. **Spec and sprint contract.** An author agent turns your answers into numbered, testable rules
   (`spec/`) and writes `spec/DONE.md`: what *done* means in plain language, each item tied to the rules
   behind it. A reviewer agent looks for places where two builders could read the spec differently, and
   you answer those questions. You approve the spec and the contract together.
3. **Generate** (`friend generate`). Fresh, separately scoped agent sessions:

   | Role | Sees | Writes | Checked by |
   |---|---|---|---|
   | planner | spec | nothing | toolchain whitelist, plan schema |
   | tester | spec, test-kit guide | `tests/generated/` | every MUST/SHOULD rule covered; every test must **fail** against a do-nothing program |
   | builder (one per language) | spec, its own folder | its own folder | build, full suite, headers, rule→file trace |
   | arbiter | spec, tests | nothing | rules on disputed tests; a test changes only with re-approval |
   | saboteur | spec, copies of the code | the copies | breaks one rule per copy; the suite must catch it |

   The tester never sees the code and the builders never see the tests or each other's code, so a passing
   suite means independent readings of the spec agree. A policy hook confines every tool call to the role's
   scope, and after every step a hash of everything outside that scope must be unchanged.
4. **Proof** (`friend verify`). The checks are re-run with no agent involved, and each contract item is
   checked off only if every rule it cites has passing tests in every language. The evidence is written to
   `.friend/proof.md`.

**Changing the spec edits instead of rebuilding.** After a spec change, `friend generate` works out which
rules were added, changed or removed, and the tester and builders edit the existing tests and code for just
those rules (`--rebuild` starts over). After every build friend writes `CONTEXT.md`, a map of which file
implements which rule.

Generated code is never edited by hand: change the spec and regenerate. `friend verify` detects hand edits.

## Commands

| Command | What it does |
|---|---|
| `friend` (or `friend new`) | Start here: idea → interview → spec + contract → approval → build → proof. Makes its own folder (or uses the current one if it's empty) |
| `friend new --from spec.md` | Start from a spec you already wrote as one document; friend keeps your rule IDs and asks only about gaps |
| `friend generate [--fresh] [--rebuild] [--yes] [--budget USD]` | Build tests and code from the approved spec (`--yes` auto-approves the plan and tests) |
| `friend verify` | Prove the sprint contract; writes `.friend/proof.md` |
| `friend run TARGET [args]` | Run a generated program |
| `friend status` · `friend report` | Where a run is and what happened |
| `friend spec refine "..."` · `friend spec check` · `friend spec contract` | Change the spec, find ambiguities, (re)write the contract |
| `friend questions` · `friend answer ID "decision"` | Open questions from reviews and runs |
| `friend clean [--all]` | Remove generated code (`--all` also removes the plan and tests) |
| `friend doctor` | Check Claude Code, login, toolchains, spec and approvals |

Exit codes are stable for scripting: 0 ok · 1 failed · 2 blocked on a spec question · 3 refused ·
4 waiting for you · 5 paused (budget) · 130 interrupted. `--json` on any command gives machine output.

## What it can build

**Command-line programs** in **Python, TypeScript or JavaScript**, driven by arguments, stdin and stdout.
Several languages can be built from one spec and are tested against each other. Programs can talk to
hand-written services you provide, like the messaging example's server. friend does not generate GUIs,
web front-ends or servers.

## Evidence

**Messaging app** (`examples/messaging/`): a spec of 75 MUST/SHOULD rules for a client with login, an
offline outbox, reconnect and group chats, plus a hand-written server. The **held-out suite**
`tests/conformance/` was written by a person and is never shown to any agent.

| Check | Python | TypeScript |
|---|---|---|
| Generated suite (87 tests covering 68 rules; 7 declared untestable) | 87/87 | 87/87 |
| Held-out suite | 19/19 | 19/19 |
| Mixed Python ↔ TypeScript runs | 24/24 generated, 6/6 held-out | |
| Mutation audit (planted bugs caught) | 100% | |

An earlier experiment, run on a smaller version of the spec, gave the same spec to one Claude session
with no harness, 3 times. It also passed everything, so for a spec this precise the harness's value is
the evidence (independent tests, the held-out suite, the proof) and the precise spec itself, not better
code. Whether the pipeline beats a solo agent on harder specs is untested (`evals/solo_baseline.py`
reproduces the comparison).

## Limitations

- **Command-line programs only.**
- **The sandbox is a policy hook plus an integrity hash, not a container.** It blocks out-of-scope reads,
  writes and arbitrary interpreters, and detects changes outside scope, but a program run by a builder is
  not isolated. Run friend on projects and machines you trust.
- **Every role uses the same model**, so the human-written held-out suite remains the strongest
  independent check where you have one.
- **Single runs are evidence, not statistics.** Pass rates over many fresh runs are not measured yet.

## Development

```sh
python3 friend/selftest/test_harness.py     # 34 tests, scripted fake agent, no API calls (also runs in CI)
```

## License

MIT
