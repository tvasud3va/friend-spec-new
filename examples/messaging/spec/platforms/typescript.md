# Platform: TypeScript client (`typescript`)

Platform specs choose *how*, never *what*. Behaviour is defined by `core/` and `extensions/`;
if anything here seems to contradict them, core wins and the conflict is a spec bug.

- **TS-1** (MUST) TypeScript compiled by the repository's pinned compiler (`tools/node_modules/.bin/tsc`, TypeScript 5.6) and run on Node.js 20+. Runtime code uses Node built-in modules only (`node:net`, `node:fs`, `node:readline`, `node:crypto`, …). No npm runtime dependencies; do not run `npm`.
- **TS-2** (MUST) Layout: sources in `clients/typescript/src/`, entry `src/main.ts`; compiled output in `clients/typescript/dist/` (entry `dist/main.js`). Launched as `node clients/typescript/dist/main.js [options]`; it must work from any working directory.
- **TS-3** (MUST) `clients/typescript/package.json` is exactly `{"type": "module", "private": true}` and `clients/typescript/tsconfig.json` uses: `"target": "ES2022"`, `"module": "NodeNext"`, `"moduleResolution": "NodeNext"`, `"strict": true`, `"rootDir": "src"`, `"outDir": "dist"`, `"typeRoots": ["../../tools/node_modules/@types"]`, `"types": ["node"]`, `"include": ["src"]`. Relative imports use `.js` extensions (NodeNext ESM).
- **TS-4** (MUST) The build command `tools/node_modules/.bin/tsc -p clients/typescript` succeeds with zero errors.
- **TS-5** (MUST) Networking uses `net.createConnection`; incoming bytes are buffered and split on `\n` (a frame may arrive split across or combined within `data` chunks). Stdin is read from `process.stdin` `data` events and split on `\n` yourself (do **not** use `readline`: it also splits on a lone `\r`, violating I-3). Stdin is buffered as raw bytes and split on 0x0A **before** decoding (a chunk may end mid-character); each line is decoded with `new TextDecoder("utf-8", { fatal: false, ignoreBOM: true })` (I-3).
- **TS-6** (MUST) Durable state is a JSON file `state.json` in the data directory. Every change rewrites it atomically with synchronous fs calls: write `state.json.tmp`, `fsync`, then `rename` over `state.json` (C-1, C-2).
- **TS-7** (MUST) Default data directory: `~/.message-friend/typescript`.
- **TS-8** (MUST) Message IDs come from `crypto.randomUUID()`.
- **TS-9** (MUST) Body length (P-8) is measured in code points, i.e. `[...body].length`, **not** `body.length` (which counts UTF-16 units and would disagree with other clients for emoji). The whitespace test (P-8) uses an explicit set of the code points listed in P-8, **not** `/\S/`, `\s` or `String.prototype.trim()` (which treat U+FEFF as whitespace and U+0085 as not).
- **TS-10** (MUST) Stdin commands are processed strictly one at a time in order (I-7) even though stdin data arrives asynchronously.
- **TS-11** (SHOULD) Code is split into small modules by concern (protocol/framing, storage, client state machine, CLI).
