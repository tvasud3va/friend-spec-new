# Platform: JavaScript on Node.js (`javascript`)

Platform specs choose *how*, never *what*. If anything here seems to contradict `core/`, core wins.
Rule prefix: `JS`.

- **JS-1** (MUST) Node.js 20+ built-in modules only, plain JavaScript ES modules with JSDoc types. No npm dependencies, no build step.
- **JS-2** (MUST) Entry point `{generated}/javascript/main.js` with `{generated}/javascript/package.json` exactly `{"type": "module", "private": true}`, launched as `node {generated}/javascript/main.js [args]`. It must work from any working directory.
- **JS-3** (MUST) Every stdout line is written whole and immediately (`process.stdout.write(line + "\n")`).
- **JS-4** (MUST) If the program reads lines from stdin, it splits `process.stdin` data on `\n` itself (`readline` also splits on a lone `\r`) and processes lines strictly one at a time, in order.
- **JS-5** (MUST) Text lengths in the spec are Unicode code points: `[...s].length`, not `s.length`.
- **JS-6** (SHOULD) Small modules by concern.
