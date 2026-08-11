# zed-perf-bench (zpb)

Evidence-grade before/after memory and CPU benchmarks for the [Zed](https://zed.dev)
editor, across a fixed set of standard scenarios. Built for one purpose:
measure first, then optimize — so a Zed memory/perf PR can point at real
numbers instead of "feels lighter."

## Quickstart

```sh
python3 -m venv .venv
.venv/bin/pip install -e .

# See what's available
.venv/bin/zpb list

# Populate real-world project fixtures (one-time, does network clones)
./fixtures/fetch.sh

# Run one scenario, 3 reps, against a specific Zed build
.venv/bin/zpb run --zed /Applications/Zed.app --label baseline --scenario 01-cold-start-empty

# Run everything
.venv/bin/zpb run --zed /Applications/Zed.app --label baseline --all

# After making a change to Zed and rebuilding it, run again under a new label
.venv/bin/zpb run --zed /path/to/target/zed --label my-change --all

# Compare the two result sets
.venv/bin/zpb compare results/<ts>-baseline results/<ts>-my-change
```

`--zed` accepts either a `.app` bundle (macOS — resolved to
`Contents/MacOS/zed`) or a direct path to the `zed` binary.

## Methodology

**Sampling.** A background thread samples the Zed process and all of its
recursive children at 1 Hz (configurable via `--sample-interval`) using
`psutil`. Each sample records per-process RSS and CPU%, tagged by role:
`zed` (the main process), `language-server` (matched against known LSP
binary/module names — see `zpb/sampler.py`), or `child-other` for
anything else. Tree RSS (the sum across all processes) is the primary
metric; per-tag breakdowns are kept so you can tell "the editor got
heavier" from "rust-analyzer got heavier."

**Startup heuristic.** There is no window-paint or "workspace ready"
instrumentation in v0. Instead, a run is considered "started" once the
combined CPU usage of the whole process tree stays below 10% for 3
consecutive 1-second samples, and `startup_seconds` is the wall-clock
time from process spawn to that point. This is a heuristic, not a ground
truth — a genuinely CPU-quiet-but-still-loading state (rare, but
possible) would be misread as "settled." Treat `startup_seconds` as
directionally useful, not as a precise TTI number.

**Settle window.** After startup is detected, the harness waits
`settle_seconds` (per-scenario, default 60) and takes `rss_settle_mb` as
the **median** RSS over that window (median, not mean, so a single GC
spike or LSP restart doesn't skew the number) and `cpu_avg_settle_pct` as
the mean CPU% over the same window.

**Soak window (optional).** If `soak_seconds > 0`, the harness keeps
sampling for that long afterward and fits a linear regression of RSS
(MB) against elapsed time (minutes) — the slope is `rss_growth_mb_per_min`,
a leak signal. `rss_soak_end_mb` is the median of the last few soak
samples. `05-idle-soak.toml` is the scenario built for this.

**Median-of-N.** `--runs` (default 3) repeats a scenario end-to-end and
aggregates: each metric gets a `median`, a `stdev`, and a `noisy` flag
(set when `stdev / median > 0.10`). Compare on the median; distrust a
metric flagged `noisy` until you've re-run it with more reps.

**Config isolation.** Zed's documented `--user-data-dir <DIR>` flag ("use
a custom directory for all user data: database, extensions, logs";
see `zed --help` / the [CLI reference](https://zed.dev/docs/reference/cli.md))
is used to give every single run its own throwaway data directory, deleted
afterward. This isolates the extensions/db/log state that could otherwise
leak between runs (e.g. an extension re-indexing on the second run because
the first run already primed its cache). It does **not** isolate
`settings.json` / `keymap.json` — those are read from your normal Zed
config location regardless. Pin your settings (theme, font size, enabled
extensions, AI features) before a before/after comparison; a settings
change between runs is a confound this harness cannot see.

**Teardown / PID hygiene.** Every run ends with: SIGTERM to the main
process, a 10s grace period, SIGKILL if it's still alive, then a check
for any surviving children (an orphaned language server after the editor
exits is itself a finding, not just cleanup noise — it's recorded under
`orphans` in the result JSON). If anything is still alive after that, the
harness raises rather than silently leaking a process. No process may
survive a `zpb run` invocation.

**Results.** Each `zpb run` invocation writes one JSON file per scenario
to `results/<UTC-timestamp>-<label>/<scenario>.json`, containing every
raw sample, per-run computed metrics, the aggregate across runs, and host
info (platform, physical RAM, macOS version, Zed version via
`<binary> --version`).

## Adding a scenario

Drop a new `scenarios/NN-name.toml`:

```toml
name = "06-my-scenario"
description = "What this measures and why."
project_path = "some-fixture-dir"   # relative to fixtures/, "" = no project
zed_args = []                        # extra CLI args passed to zed

[env]
# SOME_VAR = "value"

[phases]
settle_seconds = 60
soak_seconds = 0
startup_timeout = 120
```

All fields under `[phases]` are optional and default to the values shown
above. If the scenario opens a real project, add it to `fixtures/fetch.sh`
(pinned SHA, idempotent) and document it in `fixtures/README.md`.

## Limitations (v0)

- **No synthetic keystrokes.** These scenarios measure load/idle/soak
  behavior, not typing/scrolling/editing under load. A CPU or memory
  regression that only shows up while actively editing won't appear here.
- **Run on AC power.** Thermal throttling on battery skews CPU% numbers
  and can indirectly affect timing-sensitive metrics like `startup_seconds`.
- **Close other heavy apps.** This harness doesn't isolate against
  system-wide memory pressure or CPU contention from unrelated processes.
- **Thermal variance.** Back-to-back runs on a laptop can run hotter than
  the first run of a session; `--runs 3` (median) mitigates this but
  doesn't eliminate it. For anything you plan to cite, let the machine
  cool between labels if you can.
- **Startup heuristic, not ground truth** (see Methodology above).
- **Settings aren't isolated** — only the data directory is (see Methodology).

## Roadmap

- v1: scripted interactions (typing, scrolling, multi-file switching) via
  Zed's CLI/scripting surface, once available.
- CI integration: run a fixed scenario subset on every PR to a Zed fork
  and fail on a memory regression threshold.
- Cross-editor comparison: same scenarios against VS Code / other editors,
  for framing ("is this a Zed problem or an inherent-to-the-workload
  problem").

## License

MIT
