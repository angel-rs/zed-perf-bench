# BASELINE.md — what `zpb run --label baseline` actually does

This document exists so a reviewer — someone on the Zed team, or anyone
being handed a `zpb compare` table in a PR — can audit the *method*
behind a baseline run without reading `zpb/*.py`. Every number and
threshold below is taken directly from the source (referenced inline);
if this doc and the code ever disagree, the code is correct and this
doc is stale.

A baseline run is just: `zpb run --zed /Applications/Zed.app --label
baseline --all` (or `--scenario <name>` for one). Everything from here
down describes what that one command does, phase by phase, for every
run of every scenario it touches.

## Anatomy of one run

Each of the `--runs` repetitions (default 3, see Aggregation below) goes
through the same six phases, in order, with no phase overlapping the
next:

1. **Spawn.** The harness first refuses to run at all if another `zed`
   process is already alive on the machine (Zed is a singleton GUI app;
   see "Run conditions" below for why). It then launches the binary
   under test with `--user-data-dir <tmp-dir>` — a freshly created
   temp directory, unique to this one run, deleted again at teardown —
   plus the scenario's `project_path` (if any) and any extra
   `zed_args` from its TOML. `--user-data-dir` isolates the
   extensions/database/log state between runs; it does **not** isolate
   `settings.json`/`keymap.json`, which are read from your normal Zed
   config on every run regardless (a confound the harness cannot see —
   pin your settings before a before/after comparison).
2. **Startup detection.** A background sampler (see Sampling below)
   starts polling immediately. The harness declares the process
   "started" the moment the *whole process tree's* combined CPU usage
   stays below **10%** for **3 consecutive 1-second samples**
   (`STARTUP_CPU_THRESHOLD_PCT` / `STARTUP_STABLE_SAMPLES` in
   `zpb/scenario.py`). This is a heuristic, not window-paint or
   "workspace ready" instrumentation — there is none in this harness.
   `startup_seconds` is the wall-clock time from spawn to that point.
   It's bounded by `startup_timeout` (per-scenario, see the table
   below); if the tree never quiets down in time, or the process exits
   first, the run is aborted and recorded as failed rather than hanging
   forever.
3. **Settle window.** Once started, the harness sleeps
   `settle_seconds` (per-scenario). `rss_settle_mb` /
   `footprint_settle_mb` are the **median** (not mean — a single GC
   spike or LSP restart shouldn't skew the number) of every sample
   taken during this whole window; `cpu_avg_settle_pct` is the mean CPU
   over the same window.
4. **Quiesce checkpoint.** Immediately after settle, the harness
   sleeps a further `quiesce_seconds` (default 5, same for every
   current scenario) and takes a *second*, separate median over just
   this short window: `rss_settled_mb` / `footprint_settled_mb`. This
   is deliberately distinct from step 3's whole-window median — an
   AWSY-style "settled" measurement. Comparing the two tells a
   reviewer whether the settle window actually converged to something
   stable, or was still trending (e.g. tail-end LSP indexing) at the
   point the harness declared it done. This checkpoint always runs,
   independent of whether soak is enabled.
5. **Soak (optional).** Only if the scenario's `soak_seconds > 0`
   (currently only `05-idle-soak.toml`, at 1800s): the harness keeps
   sampling for that long, then fits a linear regression of RSS/
   footprint (MB) against elapsed time (minutes) across the whole soak
   window. The slope is the leak signal: `rss_growth_mb_per_min` /
   `footprint_growth_mb_per_min`. `rss_soak_end_mb` /
   `footprint_soak_end_mb` is the median of the last 5 soak samples
   (`SOAK_TAIL_SAMPLES`).
6. **Teardown.** SIGTERM to the main process, up to a 10-second grace
   period (`TEARDOWN_SIGTERM_WAIT_SECONDS`), then SIGKILL if it's still
   alive. Any child process that survives the main process's exit
   (e.g. an orphaned language server) is itself detected, SIGKILLed,
   and recorded under `orphans` in the result JSON as a finding, not
   just cleanup noise — an orphan surviving teardown is evidence of a
   real bug, not a harness inconvenience. If anything is still alive a
   moment after SIGKILL, the harness raises rather than silently
   leaking a process: no process may survive a `zpb run` invocation.
   The temp `--user-data-dir` is deleted in the same step.

## Sampling

A background thread samples the root Zed process and **all of its
recursive children** at **1 Hz** (`--sample-interval`, default 1.0s),
using `psutil`. Every sample is tagged per-process:

- `zed` — the root process.
- `language-server` — matched by substring against a fixed list of
  known LSP binary/module names (`rust-analyzer`,
  `typescript-language-server`, `vtsls`, `tsserver`, `gopls`, `pyright`,
  `clangd` — `zpb/sampler.py:KNOWN_LANGUAGE_SERVERS`), checked against
  both the process name and its full command line.
- `child-other` — everything else in the tree.

Two memory metrics are recorded per sample, tree-wide and per-tag:

- **`rss_mb`** — resident set size, via `psutil`. Available everywhere.
- **`footprint_mb`** — macOS's `phys_footprint`, obtained by shelling
  out to `/usr/bin/footprint -j` (an OS-provided CLI, no install
  needed). This is the number that actually matters on macOS:
  `phys_footprint` is what Jetsam (the OS's memory-pressure killer) and
  Activity Monitor act on, whereas RSS double-counts pages shared
  between the editor and its language servers and doesn't reflect
  compressed memory. `footprint_mb` is treated as the **primary**
  memory metric whenever it's obtainable.
- **Fallback:** on a non-macOS host, or if `/usr/bin/footprint` isn't
  present, every `footprint_*` key is simply absent from the result and
  RSS is the only signal. Which one applied to a given run is recorded
  explicitly in that run's `host.footprint_source` field — never left
  to be inferred.

## What each scenario exercises, and why it exists

| # | Scenario | Exercises | Why it exists |
|---|---|---|---|
| 01 | `cold-start-empty` | Launch with no project open | Base overhead with zero project/LSP variables — the AWSY-style control measurement everything else is read against |
| 02 | `vscode-ts` | Opens `microsoft/vscode` | tsserver / JS-ecosystem language-server load on a large real-world TypeScript project |
| 03 | `zed-rust` | Opens `zed-industries/zed` | rust-analyzer indexing a large Rust workspace — the known heavy case this harness was built to catch |
| 04 | `large-file` | Opens a generated 100MB log file | Rope/render stress on a single huge buffer, no language server involved |
| 05 | `idle-soak` | Empty editor, long idle window (1800s soak) | Drift/leak detection — is RSS/footprint still climbing with zero user activity? |

`02` and `03` open real third-party source trees as fixtures rather
than synthetic projects, specifically so LSP indexing behavior is
representative of what a Zed user's machine actually does when opening
a large codebase.

## Aggregation across runs

`--runs` (default 3) repeats a scenario end-to-end that many times.
Per-metric, the harness reports `median`, `stdev`, `cv` (coefficient of
variation = `stdev / median`), and a `noisy` flag set when `cv > 0.10`
(`NOISY_THRESHOLD` in `zpb/report.py`) — the Perfherder/Pinpoint
convention: report enough for a reviewer to judge signal vs. noise, not
just a point estimate. Compare on the median; don't trust a `noisy`
metric until it's been re-run with more reps.

Every result JSON additionally carries, at the top level:

- **`harness_version`** — currently `0.2.1`. `zpb compare` prints a
  warning banner if the two sides of a comparison carry different
  versions, since their metric shapes or methodology may not line up.
- **`fixture_git_sha`** / **`fixture_dirty`** — the commit and
  dirty-working-tree status of the scenario's resolved `project_path`,
  *if* it's a git repository (null otherwise — e.g. `01`/`05` have no
  project, `04`'s fixture is a plain log file). This exists specifically
  so a run against a **locally-linked** fixture (`fixtures/fetch.sh
  --link-zed <path>` — see README "Using existing local checkouts") is
  just as auditable as one against a pinned-SHA clone: two "baseline"
  runs against the same scenario are only truly comparable if they
  opened the same source tree, and this is how a reviewer can confirm
  that after the fact instead of taking it on faith.

## What baseline does NOT do (v0)

Named explicitly, same spirit as README's Limitations section (which
this baseline flow inherits in full — read it too):

- **No synthetic interaction.** No scripted keystrokes, scrolling, or
  multi-file switching. Every scenario measures load/idle/soak
  behavior, not editing-under-load. A regression that only shows up
  while actively typing will not appear here.
- **No app installs.** Fixtures are plain source-code folders
  (`fixtures/vscode`, `fixtures/zed`, generated large files) opened as
  Zed projects — nothing is installed, built, or run inside them.
- **No allocator-level breakdown.** The harness sees whole-process RSS/
  footprint totals only; it cannot attribute growth to a specific Zed
  subsystem, buffer, or heap category.
- **Single display, foreground.** One monitor, Zed in the foreground,
  no attempt to measure background/occluded-window behavior.

## Run conditions checklist

Before starting a baseline run:

- [ ] **Quit every running Zed instance.** The harness refuses to run
      otherwise (Zed is a singleton app — see "Spawn" above for why a
      second instance can't be measured in isolation).
- [ ] **AC power connected.** Battery-mode thermal throttling skews
      CPU% and, indirectly, timing-sensitive metrics like
      `startup_seconds`.
- [ ] **Lid open / display awake.** Sleep or display-off states can
      change scheduling behavior mid-run.
- [ ] **No heavy background work** (other builds, large downloads,
      video calls) — the harness does not isolate against system-wide
      CPU/memory contention.
- [ ] **Don't touch the machine during a run.** No input is expected
      or measured; interacting with it is itself a confound the harness
      can't distinguish from Zed's own behavior.

## How results are used

```sh
zpb compare results/<ts>-baseline results/<ts>-candidate
```

produces a markdown table per scenario — one row per metric, with both
labels' medians, delta, delta%, and `N (a/b)` / `CV (a/b)` so a reader
can judge signal vs. noise without opening the raw JSON — followed by
an auto-appended **Limitations** section (the same gaps as README
Limitations, plus a note if the two sides used different footprint
sources or harness versions). That combined output is what gets pasted
into an upstream Zed PR as the evidence for a memory/perf claim.

## Estimated durations

These are **computed from the phase defaults in each scenario's TOML**,
plus explicit assumptions about the two phases the harness doesn't fix
in advance — actual startup detection time and teardown time — since
neither has been measured here (this doc was written without launching
Zed). Treat these as planning estimates, not a promise.

Assumptions:
- **Startup detection:** ~5s for scenarios with no language server
  (`01`, `04`, `05`) — the CPU-quiet heuristic should catch an editor
  with nothing to index almost immediately. ~15s for `02`/`03`,
  optimistically assuming the tree dips below the 10%-CPU threshold for
  3s at some point while tsserver/rust-analyzer are still ramping up.
  This is the least certain assumption in this table: if indexing keeps
  the tree above 10% CPU continuously, startup detection could take
  much longer, bounded only by `startup_timeout` — an upper bound for
  each is given alongside the typical estimate.
- **Teardown:** ~2s (SIGTERM handled promptly), well under the 10s
  grace cap — the cap is a ceiling for a hung process, not the expected
  case.

| # | Scenario | settle | quiesce | soak | assumed startup | assumed teardown | ≈ per run | ≈ ×3 runs |
|---|---|---|---|---|---|---|---|---|
| 01 | cold-start-empty | 60s | 5s | 0s | 5s | 2s | 72s (1m12s) | 3m36s |
| 02 | vscode-ts | 90s | 5s | 0s | 15s (cap 120s) | 2s | 112s (1m52s), up to 3m37s | 5m36s, up to ~10m51s |
| 03 | zed-rust | 120s | 5s | 0s | 15s (cap 180s) | 2s | 142s (2m22s), up to 5m7s | 7m6s, up to ~15m21s |
| 04 | large-file | 45s | 5s | 0s | 5s | 2s | 57s | 2m51s |
| 05 | idle-soak | 60s | 5s | 1800s | 5s | 2s | 1872s (31m12s) | 1h33m36s |

**`--all --runs 3` total: ≈1h52m45s typical**, up to **≈2h6m15s** if
`02`/`03` each run at their pessimistic startup-detection bound instead
of the optimistic one. The soak scenario (`05`) dominates the total by
itself — its 1800s soak window, alone, ×3 runs is ~93.5 minutes, ~83%
of the whole batch. Plan a baseline session accordingly; running `05`
on its own, separately from `01`–`04`, is a reasonable way to keep an
iteration loop shorter while still collecting the leak signal
periodically.
