# Fixtures

Real-world project checkouts and generated large files used by the
scenarios in `../scenarios/`. Not checked into git (see `../.gitignore`) —
run `fetch.sh` to populate this directory.

```sh
./fetch.sh
```

This is idempotent: it skips anything that already exists, so re-running
it after a partial failure is safe.

## Using an existing local checkout instead

If you already have a local clone of `zed` or `vscode` (e.g. the working
copy you're benchmarking changes against), point `fetch.sh` at it instead
of cloning a second copy:

```sh
./fetch.sh --link-zed /path/to/your/zed
./fetch.sh --link-vscode /path/to/your/vscode
```

This symlinks `fixtures/zed` (or `fixtures/vscode`) to `<path>` — no
network traffic, no second checkout on disk. The target must already be
a git repository; it's used as-is, on whatever commit it's checked out
to, not the pinned SHA below. `zpb run` records that commit's
`fixture_git_sha` and whether the tree was dirty (`fixture_dirty`) in
every result JSON, so a run against a linked fixture stays auditable.
See `../BASELINE.md`. Both flags are idempotent the same way as a
regular fetch: if `fixtures/zed` (or `vscode`) already exists — as a
directory or a symlink — `fetch.sh` skips it with a message rather than
overwriting it.

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
