# [DRAFT — child PR 1 of 3]

**Title:** Add buffer-store diagnostics to the periodic memory-usage log

**Branch (on angel-rs/zed):** `memory-observability/buffer-store-diagnostics`

---

Part of the memory observability initiative discussed in #PARENT (child PR 1 of 3).

## Problem

Since #58999, Zed logs resident/virtual memory every 30s and flags significant jumps, and #61283 enriched those jumps with worktree-store diagnostics. But when memory grows because of buffer state — retained CRDT operations, deep undo stacks — the log can't say so, and reports like #54909 / #59711 stay unattributable from the field.

## Change

- Add `BufferStore::diagnostics()` returning summed counters across open buffers: buffer count, total retained `operations` entries, total `undo_stack` entries, largest single buffer's counts. Mirrors the shape of `WorktreeStoreDiagnostics` (`crates/project/src/worktree_store.rs`).
- Log it from the existing significant-change branch in `start_memory_usage_logging` (`crates/zed/src/reliability.rs`), next to the worktree diagnostics.

Log-only: no behavior change, no new dependencies, no telemetry (log file only, same as the existing heartbeat).

## Evidence

Harness: [zed-perf-bench](https://github.com/angel-rs/zed-perf-bench), scenario `03-zed-rust` plus a scripted edit workload; table shows the new log line attributing a synthetic growth run, and `zpb compare` confirming the logging itself has no measurable overhead (median of 3 runs, host specs attached).

<!-- paste zpb compare table here before opening -->

## Release Notes:

- N/A (developer-facing logging improvement)
