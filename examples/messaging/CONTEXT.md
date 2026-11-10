# Context: message-friend

Written by friend after every run from its own records. Do not edit; it is replaced.

## Where things are

| Path | What | Who writes it |
|---|---|---|
| `spec/` | the specification: 96 rules; the source of truth | you (and friend's spec writer, with your approval) |
| `spec/DONE.md` | the sprint contract: what done means | you |
| `plan.json` | build targets | planner agent, approved by you |
| `tests/generated/` | the test suite, written from the spec without seeing code | tester agent, approved by you |
| `clients/python/` | the `python` program (run: `friend run python`) | builder agent; never edit by hand |
| `clients/typescript/` | the `typescript` program (run: `friend run typescript`) | builder agent; never edit by hand |
| `approvals.json` | what you approved, by content hash | friend |
| `.friend/` | run logs, proof of done, reports (not committed) | friend |

### `python`: which file implements which rules

- `clients/python/cli.py`: I-1, I-2, I-11, P-1, PY-1, PY-5, PY-9
- `clients/python/client.py`: C-2, C-3, C-4, C-5, C-6, C-7, C-8, C-9, C-10, C-11, C-12, C-13, C-14, C-15, C-16, C-17, C-18, C-19, C-20, C-22, C-23, C-24, C-25, C-26, C-27, I-4, I-6, I-9, I-12, I-13, P-2, P-4, P-5, P-7, P-8, P-9, P-11, PY-1, PY-3, PY-9, X001-1, X001-2, X001-3, X001-4, X001-5, X001-8, X001-10, X001-11
- `clients/python/main.py`: C-3, I-3, I-4, I-7, PY-1, PY-2, PY-3, PY-9
- `clients/python/output.py`: I-5, I-8, I-10, PY-1, PY-8, PY-9, X001-3, X001-4, X001-6, X001-7
- `clients/python/protocol.py`: C-21, C-25, P-1, P-2, P-3, P-7, P-8, P-9, PY-1, PY-6, PY-7, PY-9, X001-1
- `clients/python/storage.py`: C-1, C-2, C-19, PY-1, PY-4, PY-9, X001-2, X001-5, X001-11

### `typescript`: which file implements which rules

- `clients/typescript/package.json`: TS-1, TS-3
- `clients/typescript/src/cli.ts`: I-1, I-2, I-6, I-11, I-12, P-1, TS-7, TS-11, X001-3, X001-4
- `clients/typescript/src/client.ts`: C-2, C-3, C-4, C-5, C-6, C-7, C-8, C-9, C-10, C-11, C-12, C-13, C-14, C-15, C-16, C-17, C-18, C-19, C-20, C-21, C-22, C-23, C-24, C-25, C-26, C-27, I-4, I-7, I-9, I-13, P-1, P-2, P-4, P-5, P-7, P-8, P-9, P-11, P-23, TS-5, TS-8, TS-11, X001-1, X001-2, X001-3, X001-4, X001-5, X001-8, X001-10, X001-11
- `clients/typescript/src/framing.ts`: C-21, I-3, P-2, P-3, TS-5, TS-11
- `clients/typescript/src/main.ts`: C-3, I-2, I-3, I-4, I-7, TS-2, TS-5, TS-10, TS-11
- `clients/typescript/src/output.ts`: I-5, I-8, I-10, TS-11, X001-4, X001-6, X001-7
- `clients/typescript/src/storage.ts`: C-1, C-2, I-1, TS-6, TS-11, X001-2, X001-6
- `clients/typescript/src/validate.ts`: P-7, P-8, TS-9, TS-11, X001-1
- `clients/typescript/tsconfig.json`: TS-1, TS-2, TS-3, TS-4

## Last build

- run `20261109-155634-f818`: **passed**, spec `3e425447f2a7`, $2.72
- edited the existing code for a spec change from `dbd458368043`: added X001-1, X001-2, X001-3, X001-4, X001-5, X001-6, X001-7, X001-8, X001-9, X001-10, X001-11
- mutation audit: tests caught 100% of deliberately broken copies

## How to change it

- Change behaviour: `friend spec refine "..."` (or edit `spec/`), `friend approve spec`, then `friend generate`. Existing tests and code are edited for the changed rules, not rebuilt.
- Check it: `friend verify` (writes `.friend/proof.md`).
