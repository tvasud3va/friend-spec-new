# Platform: TypeScript (`typescript`)

Platform specs choose *how*, never *what*. If anything here seems to contradict `core/`, core wins.
Rule prefix: `TS`.

- **TS-1** (MUST) TypeScript compiled by the project's pinned compiler `tools/node_modules/.bin/tsc` and run on Node.js 20+. Runtime code uses Node built-in modules only (`node:fs`, `node:net`, `node:readline`, `node:crypto`, …). No npm runtime dependencies; do not run `npm`.
- **TS-2** (MUST) Sources in `{generated}/typescript/src/` (entry `src/main.ts`), compiled to `{generated}/typescript/dist/` (entry `dist/main.js`), launched as `node {generated}/typescript/dist/main.js [args]`. It must work from any working directory.
- **TS-3** (MUST) `{generated}/typescript/package.json` is exactly `{"type": "module", "private": true}`. `tsconfig.json` uses `"target": "ES2022"`, `"module": "NodeNext"`, `"moduleResolution": "NodeNext"`, `"strict": true`, `"rootDir": "src"`, `"outDir": "dist"`, `"typeRoots": ["../../tools/node_modules/@types"]`, `"types": ["node"]`, `"include": ["src"]`. Relative imports use `.js` extensions.
- **TS-4** (MUST) `tools/node_modules/.bin/tsc -p {generated}/typescript` succeeds with zero errors.
- **TS-5** (MUST) Every stdout line is written whole and immediately (`process.stdout.write(line + "\n")`).
- **TS-6** (MUST) If the program reads lines from stdin, it splits `process.stdin` data on `\n` itself (`readline` also splits on a lone `\r`) and processes lines strictly one at a time, in order.
- **TS-7** (MUST) Text lengths in the spec are Unicode code points: `[...s].length`, not `s.length`.
- **TS-8** (SHOULD) Small modules by concern.
