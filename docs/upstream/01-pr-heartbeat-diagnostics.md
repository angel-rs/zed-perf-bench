# [DRAFT — child PR 1 of 3]

**Title:** Add buffer-store diagnostics to the periodic memory-usage log

**Branch (on angel-rs/zed):** `memory-observability/buffer-store-diagnostics`

---

Part of the memory observability initiative discussed in #PARENT (child PR 1 of 3).

## Problem

Since #58999, Zed logs resident/virtual memory every 30s and flags significant jumps, and #61283 enriched those jumps with worktree-store diagnostics. But when memory grows because of buffer state — retained CRDT operations, deep undo stacks — the log can't say so, and reports like #54909 / #59711 stay unattributable from the field.

## Change

- Add `BufferStoreDiagnostics` and `BufferStore::diagnostics(&self, cx: &App)` (`crates/project/src/buffer_store.rs:35`, `:1028`), summing across open buffers: `buffer_count`, `total_operations`, `total_undo_entries`, and `largest_buffer_operations`. Mirrors the shape of `WorktreeStoreDiagnostics` (`crates/project/src/worktree_store.rs`).
- Add `text::Buffer::operation_count()` and `undo_stack_len()` (`crates/text/src/text.rs:1352`, `:1356`), next to the existing `operations()` getter, since `History`'s `operations`/`undo_stack` fields had no public length accessors.
- Add `log_buffer_store_diagnostics` (`crates/zed/src/reliability.rs:237`) and call it from the same significant-change branch as `log_worktree_diagnostics` (`:110`), same dedup-by-`entity_id`-and-aggregate shape, one `log::info!` line.

Log-only: no behavior change, no new dependencies, no telemetry (log file only, same as the existing heartbeat).

## Evidence

Harness: [zed-perf-bench](https://github.com/angel-rs/zed-perf-bench). A/B run on GitHub Actions macos-14 (arm64): upstream `main` vs this branch, both built from source with identical flags, 3 scenarios x 2 runs — [full run](https://github.com/angel-rs/zed-perf-bench/actions/runs/31650863164). All deltas fall within the harness's measured A/A noise floor (±0.2-1.8% RSS, ±0.8-4.4% phys_footprint — [null test](https://github.com/angel-rs/zed-perf-bench/actions/runs/31618916212)): the logging adds no measurable memory or startup overhead.

<details>
<summary>Full before/after comparison table</summary>

# Compare: 20260813T001550Z-ci-upstream-main-62a6e73 vs 20260813T002407Z-ci-pr1-diagnostics-62a6e73

## 01-cold-start-empty

| metric | 20260813T001550Z-ci-upstream-main-62a6e73 | 20260813T002407Z-ci-pr1-diagnostics-62a6e73 | Δ | Δ% | N (a/b) | CV (a/b) |
|---|---|---|---|---|---|---|
| cpu_avg_settle_pct | 0.00 | 0.00 | +0.00 | n/a | 2/2 | n/a/n/a |
| footprint_peak_mb | 73.36 | 71.03 | -2.33 (improvement) | -3.2% | 2/2 | 0.012/0.032 |
| footprint_settle_by_tag_mb[zed] | 66.59 | 66.32 | -0.27 (improvement) | -0.4% | 2/2 | 0.012/0.013 |
| footprint_settle_mb | 66.59 | 66.32 | -0.27 (improvement) | -0.4% | 2/2 | 0.012/0.013 |
| footprint_settled_mb | 66.57 | 66.15 | -0.42 (improvement) | -0.6% | 2/2 | 0.013/0.012 |
| rss_peak_mb | 142.50 | 142.34 | -0.16 (improvement) | -0.1% | 2/2 | 0.001/0.004 |
| rss_settle_by_tag_mb[zed] | 141.75 | 141.70 | -0.05 (improvement) | -0.0% | 2/2 | 0.002/0.004 |
| rss_settle_mb | 141.75 | 141.70 | -0.05 (improvement) | -0.0% | 2/2 | 0.002/0.004 |
| rss_settled_mb | 141.75 | 141.58 | -0.17 (improvement) | -0.1% | 2/2 | 0.002/0.004 |
| startup_seconds | 2.40 | 2.42 | +0.02 | +0.8% | 2/2 | 0.003/0.021 |

## 03b-ripgrep-rust

| metric | 20260813T001550Z-ci-upstream-main-62a6e73 | 20260813T002407Z-ci-pr1-diagnostics-62a6e73 | Δ | Δ% | N (a/b) | CV (a/b) |
|---|---|---|---|---|---|---|
| cpu_avg_settle_pct | 0.00 | 0.00 | +0.00 | n/a | 2/2 | n/a/n/a |
| footprint_peak_mb | 73.71 | 71.32 | -2.40 (improvement) | -3.3% | 2/2 | 0.000/0.065 |
| footprint_settle_by_tag_mb[zed] | 66.10 | 66.89 | +0.78 (regression) | +1.2% | 2/2 | 0.006/0.010 |
| footprint_settle_mb | 66.10 | 66.89 | +0.78 (regression) | +1.2% | 2/2 | 0.006/0.010 |
| footprint_settled_mb | 66.07 | 66.81 | +0.74 (regression) | +1.1% | 2/2 | 0.005/0.012 |
| rss_peak_mb | 152.72 | 152.66 | -0.05 (improvement) | -0.0% | 2/2 | 0.003/0.002 |
| rss_settle_by_tag_mb[zed] | 151.99 | 151.96 | -0.03 (improvement) | -0.0% | 2/2 | 0.003/0.003 |
| rss_settle_mb | 151.99 | 151.96 | -0.03 (improvement) | -0.0% | 2/2 | 0.003/0.003 |
| rss_settled_mb | 151.99 | 151.94 | -0.05 (improvement) | -0.0% | 2/2 | 0.003/0.002 |
| startup_seconds | 2.35 | 2.31 | -0.04 | -1.5% | 2/2 | 0.072/0.040 |

## 04-large-file

| metric | 20260813T001550Z-ci-upstream-main-62a6e73 | 20260813T002407Z-ci-pr1-diagnostics-62a6e73 | Δ | Δ% | N (a/b) | CV (a/b) |
|---|---|---|---|---|---|---|
| cpu_avg_settle_pct | 0.00 | 0.00 | +0.00 | n/a | 2/2 | n/a/n/a |
| footprint_peak_mb | 311.01 | 310.00 | -1.02 (improvement) | -0.3% | 2/2 | 0.013/0.008 |
| footprint_settle_by_tag_mb[zed] | 306.91 | 305.44 | -1.46 (improvement) | -0.5% | 2/2 | 0.001/0.006 |
| footprint_settle_mb | 306.91 | 305.44 | -1.46 (improvement) | -0.5% | 2/2 | 0.001/0.006 |
| footprint_settled_mb | 306.76 | 305.21 | -1.55 (improvement) | -0.5% | 2/2 | 0.002/0.005 |
| rss_peak_mb | 412.62 | 404.10 | -8.52 (improvement) | -2.1% | 2/2 | 0.002/0.027 |
| rss_settle_by_tag_mb[zed] | 411.93 | 403.38 | -8.55 (improvement) | -2.1% | 2/2 | 0.001/0.027 |
| rss_settle_mb | 411.93 | 403.38 | -8.55 (improvement) | -2.1% | 2/2 | 0.001/0.027 |
| rss_settled_mb | 411.89 | 403.38 | -8.52 (improvement) | -2.1% | 2/2 | 0.002/0.027 |
| startup_seconds | 2.42 | 2.36 | -0.06 | -2.5% | 2/2 | 0.011/0.019 |

## Limitations

- **No USS/PSS.** `psutil` cannot report unique or proportional set size on macOS, only RSS (and, when available, phys_footprint). RSS double-counts pages shared between the editor and its language servers — a real confound for a multi-process tree like this one.
- **No allocator-level or per-subsystem breakdown.** This harness sees whole-process totals only; it cannot attribute growth to a specific Zed subsystem, buffer, or heap category the way an in-process allocator hook could (Firefox/Chromium call this class of gap `heap-unclassified` rather than pretend it doesn't exist — same idea here).
- **RSS is not Jetsam-equivalent.** macOS's memory-pressure killer and Activity Monitor act on `phys_footprint`, not RSS; RSS does not reflect compressed memory and over-counts shared pages relative to it.
- **Footprint source for these results**: phys_footprint (/usr/bin/footprint).
- **Settings aren't isolated.** Only `--user-data-dir` is per-run; `settings.json`/`keymap.json` are read from the normal Zed config location on both sides of a comparison (see README Methodology → Config isolation).

</details>

## Release Notes:

- N/A (developer-facing logging improvement)
