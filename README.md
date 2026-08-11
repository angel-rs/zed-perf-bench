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

### Using existing local checkouts

If you already have local clones of `zed` and/or `vscode`, point
`fetch.sh` at them instead of cloning a second copy:

```sh
./fixtures/fetch.sh --link-zed ~/Development/zed
./fixtures/fetch.sh --link-vscode ~/Development/vscode
```

This symlinks the fixture in place — zero new downloads — and each
`zpb run` records that checkout's commit and dirty state in the result
JSON (`fixture_git_sha` / `fixture_dirty`), so a baseline run against a
local fixture stays auditable. See [BASELINE.md](BASELINE.md) for
exactly what a baseline run does, phase by phase, and
[fixtures/README.md](fixtures/README.md) for fixture details.

## Methodology

This harness's measurement conventions are not invented from scratch — they
follow the two reference implementations for this kind of work:
[Firefox "Are We Slim Yet" (AWSY)](https://firefox-source-docs.mozilla.org/performance/memory/awsy.html)
for the settled-measurement and median-of-N methodology, and
[Chromium memory-infra](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/memory-infra/README.md)
for per-process-plus-aggregate reporting. Where this harness diverges from
either, it's called out explicitly below and in Limitations, the same way
those projects name their own instrumentation gaps (Chromium's
`heap-unclassified`) rather than imply false precision.

**Sampling.** A background thread samples the Zed process and all of its
recursive children at 1 Hz (configurable via `--sample-interval`) using
`psutil`. Each sample records per-process RSS and CPU%, tagged by role:
`zed` (the main process), `language-server` (matched against known LSP
binary/module names — see `zpb/sampler.py`), or `child-other` for
anything else. Tree RSS (the sum across all processes) is kept for
continuity; per-tag breakdowns are kept so you can tell "the editor got
heavier" from "rust-analyzer got heavier."

**phys_footprint (primary metric on macOS, when available).** RSS is
misleading on macOS: it double-counts pages shared between processes and
doesn't reflect compressed memory, while Jetsam (the OS's memory-pressure
killer) and Activity Monitor act on `phys_footprint` — the figure
`task_info(TASK_VM_INFO)` exposes. `psutil` has no way to get it. This
harness gets it by shelling out to `/usr/bin/footprint` (an OS-provided
CLI, no install needed), which — verified empirically on macOS 26.5.2 /
Darwin 25.5 — works unprivileged against a same-user process and returns
clean JSON via `-j`. Two alternatives were tried and rejected: raw ctypes
`task_info(TASK_VM_INFO)` needs `task_for_pid` entitlements this harness
doesn't have and fails against any process other than itself; `top -pid`
reports a resident-size-derived "MEM" figure, not `phys_footprint`, so it
doesn't solve the double-counting problem it's meant to fix. When
`footprint` is available, every RSS-based metric below gets a
`footprint_*` counterpart (`footprint_settle_mb`, `footprint_peak_mb`,
etc.) computed the same way, over `phys_footprint` instead of RSS. When
it isn't (non-macOS, or `/usr/bin/footprint` missing), those keys are
simply absent from the result and RSS remains the only signal — see
Limitations.

**Startup heuristic.** There is no window-paint or "workspace ready"
instrumentation in v0. Instead, a run is considered "started" once the
combined CPU usage of the whole process tree stays below 10% for 3
consecutive 1-second samples, and `startup_seconds` is the wall-clock
time from process spawn to that point. This is a heuristic, not a ground
truth — a genuinely CPU-quiet-but-still-loading state (rare, but
possible) would be misread as "settled." Treat `startup_seconds` as
directionally useful, not as a precise TTI number.

**Settle window.** After startup is detected, the harness waits
`settle_seconds` (per-scenario, default 60) and takes `rss_settle_mb` /
`footprint_settle_mb` as the **median** over that whole window (median,
not mean, so a single GC spike or LSP restart doesn't skew the number)
and `cpu_avg_settle_pct` as the mean CPU% over the same window.

**Quiesce checkpoint.** Immediately after the settle window, the harness
waits a further `quiesce_seconds` (default 5, configurable per-scenario)
and records `rss_settled_mb` / `footprint_settled_mb` as the median over
*that* short window only — an AWSY-style "settled" measurement, deliberately
distinct from the whole-settle-window median above. The two can and do
diverge (e.g. if the settle window still contains some tail-end LSP
indexing); keeping both lets a reviewer see whether "settle" actually
converged to something stable or is still trending at the point the
harness declares it done.

**Soak window (optional).** If `soak_seconds > 0`, the harness keeps
sampling after the quiesce window and fits a linear regression against
elapsed time (minutes) — the slope is `rss_growth_mb_per_min` /
`footprint_growth_mb_per_min`, a leak signal. `rss_soak_end_mb` /
`footprint_soak_end_mb` is the median of the last few soak samples.
`05-idle-soak.toml` is the scenario built for this.

**Median-of-N, with CV.** `--runs` (default 3) repeats a scenario
end-to-end and aggregates: each metric gets a `median`, a `stdev`, a `cv`
(coefficient of variation, `stdev / median`), and a `noisy` flag (set when
`cv > 0.10`) — the Perfherder/Pinpoint convention of reporting enough for
a reviewer to judge signal vs. noise, not just a point estimate. Compare
on the median; distrust a metric flagged `noisy` until you've re-run it
with more reps. `zpb compare` prints N and CV for both sides of every row
so this judgment call doesn't require opening the raw JSON.

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
info (platform, physical RAM, macOS version, footprint source, Zed
version via `<binary> --version`), plus `fixture_git_sha` / `fixture_dirty`
for the scenario's project fixture when it's a git repo (null for
non-project scenarios or non-git fixtures like `large/100mb.log`) — see
BASELINE.md for why. Every result also carries a `harness_version`
(currently `0.2.1`) — `zpb compare` warns if the two sides of a
comparison were produced by different harness versions, since their
metric shapes or methodology may not line up.

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
quiesce_seconds = 5
soak_seconds = 0
startup_timeout = 120
```

All fields under `[phases]` are optional and default to the values shown
above. If the scenario opens a real project, add it to `fixtures/fetch.sh`
(pinned SHA, idempotent) and document it in `fixtures/README.md`.

## Limitations

Named explicitly rather than left implicit — Firefox and Chromium name
their own instrumentation gaps (e.g. `heap-unclassified`) instead of
implying false precision, and this harness does the same:

- **No USS/PSS.** `psutil` cannot report unique or proportional set size
  on macOS, only RSS (and, when available, `phys_footprint`). RSS
  double-counts pages shared between the editor and its language servers
  — a real confound for a multi-process tree like this one.
- **No allocator-level or per-subsystem breakdown.** This harness sees
  whole-process totals only; it cannot attribute growth to a specific Zed
  subsystem, buffer, or heap category the way an in-process allocator
  hook could.
- **RSS is not Jetsam-equivalent.** macOS's memory-pressure killer and
  Activity Monitor act on `phys_footprint`, not RSS (see Methodology
  above for why and how this harness gets it). On a host where
  `/usr/bin/footprint` is unavailable, every `footprint_*` metric is
  simply absent from the result and RSS is the only signal — check
  `host.footprint_source` in the result JSON to see which applied to a
  given run.
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

`zpb compare` auto-appends a Limitations section to its markdown output
so a reviewer sees these caveats next to the numbers, not just here.

## Roadmap

- v1: scripted interactions (typing, scrolling, multi-file switching) via
  Zed's CLI/scripting surface, once available.
- v1: allocator-stats hooks (mimalloc) for a per-subsystem breakdown, if
  Zed's allocator exposes one — narrows the "no allocator-level breakdown"
  gap above.
- Change-point detection once enough history exists to make it worthwhile
  (e.g. [Apache Otava](https://otava.apache.org/)), instead of eyeballing
  before/after tables by hand.
- CI integration: run a fixed scenario subset on every PR to a Zed fork
  and fail on a memory regression threshold.
- Cross-editor comparison: same scenarios against VS Code / other editors,
  for framing ("is this a Zed problem or an inherent-to-the-workload
  problem").

## License

MIT
