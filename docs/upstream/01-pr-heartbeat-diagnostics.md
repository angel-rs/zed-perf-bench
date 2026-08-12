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

Harness: [zed-perf-bench](https://github.com/angel-rs/zed-perf-bench), scenario `03-zed-rust` plus a scripted edit workload; table shows the new log line attributing a synthetic growth run, and `zpb compare` confirming the logging itself has no measurable overhead (median of 3 runs, host specs attached).

<!-- paste zpb compare table here before opening -->

## Release Notes:

- N/A (developer-facing logging improvement)
