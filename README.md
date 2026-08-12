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

## Running in CI (GitHub Actions)

The primary way to run this is GitHub's own web UI — no local checkout,
no `gh` CLI, no terminal: **Actions tab → pick a workflow (`ab`, `bench`,
or `build`) → "Run workflow" → fill in the dropdown/text inputs → green
button.** Every input that has a fixed set of sane values (Zed channel,
rep count, scenario set) is a `type: choice` dropdown, so there's nothing
to typo. Once it's running, the run page's **Summary** tab shows the
result — a compare table (A vs B) or the single-channel metrics table —
without downloading anything; the raw JSON/logs are still there as a
downloadable artifact if you need them.

The `gh` CLI remains a fully-supported alternative for scripting a
dispatch or watching one from a terminal — every example below has both.
All three workflows are `workflow_dispatch`-only (never on push/PR).

### One-click A/B from source (`ab.yml`, recommended)

`.github/workflows/ab.yml` is the orchestrator: one dispatch builds
**both** sides from source in parallel and benches them against each
other, with zero run-id copy-pasting. This is what most A/B questions
("does my branch make things worse?") actually want, and it's the UI
path this whole rewrite exists for.

**UI walkthrough:** Actions → `ab` → Run workflow → set `repo_b`/`ref_b`
to your candidate branch (`repo_a`/`ref_a` default to
`zed-industries/zed@main`), pick `scenarios` and `runs` from the
dropdowns → Run workflow. Three jobs appear (`build-a`, `build-b` running
in parallel, then `bench`); when `bench` finishes, its Summary tab has
the compare table.

**`gh` CLI equivalent:**

```sh
gh workflow run ab.yml \
  -f ref_b="my-perf-branch" \
  -f label_b="my-change" \
  -f scenarios="01-cold-start-empty 03b-ripgrep-rust" \
  -f runs="2"

gh run watch                     # follow build-a, build-b, then bench
gh run list --workflow=ab.yml --limit 1 --json url --jq '.[0].url'
```

Under the hood, `build.yml` and `bench.yml` both declare `on:
workflow_call` alongside their `workflow_dispatch` trigger, so `ab.yml`
invokes them directly (`uses: ./.github/workflows/build.yml`) as
reusable workflows instead of dispatching-and-polling. Because
`workflow_call`'d jobs execute inside the *caller's* run, `build-a`,
`build-b`, and `bench` all share one `github.run_id` — which is exactly
what lets `bench`'s `build_label_a`/`build_label_b` inputs resolve their
artifacts from **the current run** (see `bench.yml`'s
`build_run_id_*`/`build_label_*` inputs below) instead of a separate
`build.yml` run id ever needing to be passed around. `ab.yml` itself has
no `workflow_call` trigger — it's the top-level entry point, not
something that gets called by anything else.

**Billing note:** this is two from-source Zed builds plus a bench run in
one dispatch — see the billing section on `build.yml` below. Dispatch it
deliberately.

### `bench.yml` — single build or brew channels

Prefer `ab.yml` for a from-source A vs. B comparison; reach for
`bench.yml` directly when you want a single-channel run, a brew-channel
comparison (`stable` vs. `preview`), or you're re-benching an artifact
from a `build.yml` run you already have sitting around.

**UI walkthrough:** Actions → `bench` → Run workflow → pick `channel_a`
from the dropdown and type a `channel_b` (or leave both `build_label_a`/
`build_label_b` set instead, to bench a from-source artifact) → Run
workflow → read the Summary tab when it's done.

**`gh` CLI equivalent:**

```sh
gh workflow run bench.yml \
  -f scenarios="01-cold-start-empty" \
  -f runs="2" \
  -f soak_seconds="600" \
  -f channel_a="stable" \
  -f channel_b=""
```

- `scenarios` — space- or comma-separated scenario names (default
  `01-cold-start-empty`). Valid names: `01-cold-start-empty`,
  `03-zed-rust`, `03b-ripgrep-rust`, `04-large-file`, `05-idle-soak`.
  `03-zed-rust` triggers a pinned shallow clone of `zed-industries/zed`
  into `fixtures/zed`; `04-large-file` triggers local generation of
  `fixtures/large/100mb.log`. `02-vscode-ts` is not wired up in CI (no
  cheap fixture path for it yet).
- `runs` — reps per scenario (choice: 1/2/3 in the UI), passed straight
  to `zpb run --runs`.
- `soak_seconds` — passed as `--soak-override` (see below); only takes
  effect if a selected scenario already has `soak_seconds > 0` (today,
  only `05-idle-soak`, whose TOML default is 1800s).
- `channel_a` — Zed channel for the first run (choice: `stable`/`preview`
  in the UI).
- `channel_b` — Zed channel for the second run. Plain text, not a
  dropdown — its most common value is `""` (GitHub Actions requires
  `type: choice` options to be non-empty strings, so it can't be one of
  the dropdown's own choices). Empty (the default) means a
  single-channel run with no compare; `channel_b="preview"` installs
  `zed@preview` alongside `zed` (stable) and runs both, followed by `zpb
  compare`.

Watch it and pull the results down once it's done:

```sh
gh run watch                     # or: gh run list --workflow=bench.yml
gh run download <run-id>         # artifact: bench-results-<run-id>/
```

**Results without downloading anything.** Every `bench.yml` run writes
its results to the run's **Summary** tab (`$GITHUB_STEP_SUMMARY`,
`if: always()`): the `compare.md` table when both channels ran, or the
single channel's `run-summary.txt` plus a key-metrics table (per
scenario: startup time, `rss_settle_mb`, `footprint_settled_mb`, all
medians) parsed straight out of the result JSONs with `jq`. The
downloadable artifact below still exists for the raw JSON and logs — the
Summary tab is for the "did this help, yes or no" answer at a glance.

The artifact contains the raw `results/` JSON, a `compare.md` if both
channels ran, `host-info.txt` (CPU model, macOS version, memory), and
`Zed.log` if Zed produced one — captured with `if: always()` so a run
where Zed fails to launch or render still leaves behind whatever
diagnostic data exists, which is itself the useful signal for judging
whether this runner class is viable at all.

**The noise caveat.** A GitHub-hosted runner is a shared VM, not
dedicated hardware — background noise and scheduling jitter are higher
and less predictable than on a machine sitting on your desk. Every
result produced by this workflow is tagged `host.ci: true` (via the
`ZPB_CI=1` env var the workflow sets), and `zpb compare` refuses to let
that pass silently: comparing a `ci: true` result against a `ci: false`
one prints a warning banner, the same pattern used for a
`harness_version` mismatch. Treat CI runs as good for **relative** A/B
comparisons *within the same workflow run* (same runner class, same
`--runs`, CV disclosed in every row) — not as a source of **absolute**
numbers to cite in an upstream PR. For that, use a dedicated machine
per BASELINE.md's run-conditions checklist.

**Billing.** This repository is private, and GitHub bills private-repo
macOS runner minutes at a **10x multiplier** against the plan's included
minutes (a `macos-14` minute here costs 10 minutes of quota). The
workflow is kept lean specifically because of this: one job, no matrix,
manual dispatch only, `timeout-minutes: 45` as a hard ceiling. A public
repo does not pay this multiplier — it gets free, effectively unlimited
minutes on GitHub-hosted standard runners (macOS included) for public
workflows.

**`--soak-override <seconds>`.** `zpb run` accepts `--soak-override`,
which overrides `soak_seconds` for any scenario whose TOML already sets
`soak_seconds > 0` — a no-op for every other scenario. This exists so a
long soak (e.g. `05-idle-soak`'s 1800s default) can be time-boxed for a
CI run's `timeout-minutes` budget without hand-editing the scenario
TOML; it works the same way outside CI, for the same reason (a quick
local leak-signal check without waiting the full 30 minutes).

### Build-from-source + bench: two isolated workflows

`ab.yml` (above) automates the whole flow below into one dispatch. Reach
for the manual version here when you want asymmetric control — e.g.
build once with `build.yml` and bench it against several different
`scenarios`/`runs` combinations without rebuilding, or bench a
`build.yml` run that's already sitting around from an earlier dispatch.

`.github/workflows/build.yml` builds a cargo package from source (Zed
itself, by default) on its own `macos-14` runner and uploads the binary
as an artifact. It's split out from `bench.yml` on purpose — divide and
conquer: a from-source build can run long and fails in a completely
different way (an OOM'd linker) than a benchmark run (a hung or
crashing editor), so each gets its own job, its own timeout, and a
build's worst case never eats into `bench.yml`'s tight 45-minute
budget:

```
┌────────────────────┐     ┌────────────────────┐
│ build.yml           │     │ build.yml           │
│ label=A, timeout=180│     │ label=B, timeout=180│   dispatched
│ (independent job,   │     │ (independent job,   │   separately,
│  runs in parallel)  │     │  runs in parallel)  │   run in parallel
└──────────┬───────────┘     └──────────┬───────────┘
           │ artifact:                   │ artifact:
           │ zed-build-A                 │ zed-build-B
           └──────────────┬──────────────┘
                           ▼
                 ┌────────────────────┐
                 │ bench.yml           │   timeout=45,
                 │ downloads both      │   never builds
                 │ artifacts, runs     │   from source
                 │ scenarios, compares │   itself
                 └────────────────────┘
```

`bench.yml`'s `channel_a`/`channel_b` (installed via brew) remain the
default path. To bench two from-source builds against each other
instead, dispatch `build.yml` twice, then feed both run ids into
`bench.yml`'s `build_run_id_a`/`build_label_a` and
`build_run_id_b`/`build_label_b` inputs:

```sh
# 1. Build channel A (e.g. upstream main) and channel B (e.g. a candidate
#    branch/fork), in parallel — two independent dispatches.
gh workflow run build.yml -f repo=zed-industries/zed -f ref=main \
  -f label=upstream-main
gh workflow run build.yml -f repo=your-fork/zed -f ref=my-perf-branch \
  -f label=my-change

# 2. Grab the run ids (newest first).
gh run list --workflow=build.yml --limit 5

# 3. Wait for both to finish (each up to timeout-minutes: 180).
gh run watch <run-id-a>
gh run watch <run-id-b>

# 4. Feed both artifacts into bench.yml as channel A and channel B.
gh workflow run bench.yml \
  -f scenarios="03b-ripgrep-rust" \
  -f runs="2" \
  -f build_run_id_a="<run-id-a>" -f build_label_a="upstream-main" \
  -f build_run_id_b="<run-id-b>" -f build_label_b="my-change"

# 5. Pull the compare table down once it's done.
gh run watch <bench-run-id>
gh run download <bench-run-id>
```

A `build_run_id_*` set without its matching `build_label_*` fails the run
early with a clear `::error::` rather than a confusing downstream
artifact-not-found — an artifact name (`zed-build-<label>`) can't be
resolved from a run id alone. The reverse — `build_label_*` set with
`build_run_id_*` left empty — is valid on purpose: it pulls
`zed-build-<label>` from the **current** run instead of a separate
`build.yml` run, which is exactly the mode `ab.yml`'s `bench` job uses
(steps 1-4 above collapse into that one case). Either way, once a
`build_label_*` is set, it also replaces the corresponding `channel_*` in
the zpb result label (`ci-<build_label_a>-<sha>` instead of
`ci-<channel_a>-<sha>`), so a from-source result is never mislabeled as a
brew channel it never touched.

**Fairness note.** `build.yml` pins the same build env
(`CARGO_BUILD_JOBS=3`, `CARGO_PROFILE_RELEASE_DEBUG=0`,
`CARGO_PROFILE_RELEASE_LTO=thin`, `CARGO_INCREMENTAL=0`) regardless of
which `repo`/`ref` a given dispatch targets, so channel A and channel B
in a from-source `bench.yml` comparison are always built identically —
the numbers are internally comparable to each other. They are **not**
comparable to an official Zed release build, whose build profile and
code-signing differ from what this harness produces.

**Gatekeeper note.** A `zed-build-*` artifact downloaded by `bench.yml`
via `actions/download-artifact` runs fine inside CI — nothing in that
path (`gh run download`, or the action itself) applies macOS's
`com.apple.quarantine` attribute, which is only ever set on files that
arrived through a browser or similar quarantine-aware download path. If
you instead pull an artifact down locally to a Mac through a browser to
poke at it by hand, macOS may quarantine it there; `xattr -d
com.apple.quarantine <path>` or right-click → Open clears that. Not a
concern for the CI-only round-trip this repo actually uses.

**7GB RAM strategy.** Two independent levers keep this fitting a
`macos-14` runner's 7GB:
- **Build side** (`build.yml`): the pinned env above exists specifically
  for this constraint. `CARGO_BUILD_JOBS=3` caps concurrent linker
  invocations — the linker, not the compiler, is what actually OOMs a
  memory-constrained build. `CARGO_PROFILE_RELEASE_DEBUG=0` and
  `CARGO_PROFILE_RELEASE_LTO=thin` keep the linked binary (and thus
  linker memory) smaller. `CARGO_INCREMENTAL=0` skips incremental-
  compilation caching that only pays off across repeated local builds,
  never a single from-scratch CI run.
- **Bench side** (`bench.yml`): `03b-ripgrep-rust` exists for the same
  reason `03-zed-rust` doesn't fit here — rust-analyzer indexing
  ripgrep's much smaller workspace runs ~1-2GB, comfortably inside a
  7GB runner, while still exercising the same rust-analyzer-indexing
  code path. `03-zed-rust`'s full zed-repo indexing stays reserved for
  dedicated/lab hardware with real headroom, per the run-conditions
  checklist in BASELINE.md.

## Methodology

The full design justification — a comparison matrix of the five industry
approaches to memory measurement (Apple, Microsoft, Google/Chromium, Mozilla)
and why this harness sits where it does, with annotated bibliography — lives
in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

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
