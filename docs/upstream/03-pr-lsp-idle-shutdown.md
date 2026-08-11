# [DRAFT — child PR 3 of 3 — DO NOT OPEN until the parent discussion blesses the design]

**Title:** Opt-in idle shutdown for language servers

**Branch (on angel-rs/zed):** `memory-observability/lsp-idle-shutdown`

---

Part of the memory observability initiative discussed in #PARENT (child PR 3 of 3, follows #CHILD2). Design was discussed in #PARENT before this PR was opened.

## Problem

Language servers dominate Zed's real-world memory footprint (rust-analyzer alone routinely reaches double-digit GB), and today they live as long as their worktree: no idle shutdown exists — servers stop only on explicit restart, worktree removal, or quit (`crates/project/src/lsp_store.rs`). On RAM-constrained machines, servers for projects you looked at hours ago keep their full footprint.

## Change

- New setting (default **off**): `"language_servers": { "idle_shutdown_minutes": null }` — when set, a periodic sweep stops language servers for worktrees that have had no visible buffers for that window, reusing the existing, already-tested `stop_language_servers_for_buffers` path (`crates/project/src/project.rs`).
- Hysteresis: a server restarted by reopening a buffer is exempt from the sweep for one full window, so edit→close→edit cycles don't thrash.
- Zero change for anyone who doesn't set the setting.

## Trade-off (stated up front)

Idle shutdown trades memory for restart latency and re-indexing on return. That's why it's opt-in and time-based per worktree, and why the evidence includes restart-cost measurements, not just the memory win.

## Evidence

Harness: [zed-perf-bench](https://github.com/angel-rs/zed-perf-bench), multi-worktree scenario: open buffers across 3 worktrees, close two, wait out the idle window. Before/after table: process-tree RSS, language-server process count, and measured reopen latency for a swept worktree. Median of 3 runs, host specs attached.

<!-- paste zpb compare table here before opening -->

## Release Notes:

- Added an opt-in setting to automatically stop language servers for worktrees after a period with no visible buffers, reducing memory usage on RAM-constrained machines.
