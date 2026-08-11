# Fixtures

Real-world project checkouts and generated large files used by the
scenarios in `../scenarios/`. Not checked into git (see `../.gitignore`) —
run `fetch.sh` to populate this directory.

```sh
./fetch.sh
```

This is idempotent: it skips anything that already exists, so re-running
it after a partial failure is safe.

## What it fetches

- `vscode/` — depth-1 clone of `microsoft/vscode` at a pinned commit SHA.
  Used by `scenarios/02-vscode-ts.toml` to load a large real-world
  TypeScript project.
- `zed/` — depth-1 clone of `zed-industries/zed` at a pinned commit SHA.
  Used by `scenarios/03-zed-rust.toml` to load a large real-world Rust
  workspace and trigger rust-analyzer indexing.
- `large/100mb.log` — generated log file with repeated, realistic log
  lines. Used by `scenarios/04-large-file.toml`.
- `large/10mb-single-line.json` — generated JSON file, single line, ~10MB.
  Not currently wired to a scenario; kept as a fixture for future
  minified/single-line-file scenarios.

## Why pinned SHAs

Upstream commit history moves. Pinning the exact commit means a benchmark
run today and a benchmark run in six months open the *same* source tree,
so a memory/CPU delta is attributable to the Zed build under test, not to
the fixture project having changed shape underneath it. Bump the SHAs in
`fetch.sh` deliberately (with a comment on why) rather than tracking
`HEAD`.
