# [DRAFT — post as GitHub Discussion in zed-industries/zed]

> Post under: Discussions → category "Features & Ideas" (or "General" if staff prefers).
> After posting, replace `#PARENT` in the child PR drafts with the real discussion number.
> Gate before posting: make angel-rs/zed-perf-bench public — it is cited throughout.

**Title:** You can't improve what you can't measure — a memory observability & footprint initiative

---

Hi! I'd like to propose (and build) a small, measurement-first series of contributions around Zed's memory footprint, coordinated here so nothing lands as a surprise PR.

## Why

Memory is currently one of Zed's most-reported pain points, and the hard part doesn't seem to be writing fixes — it's reproducing and attributing the growth. A few reference points:

- Open S1/S2 reports of unbounded growth: #58190 (hundreds of GB after a multi-hour hang), #59711, #54909, #55570, #35780, #46474 (leftover Node.js processes), #38927.
- In discussion #59303, @SomeoneToIgnore wrote that the team is "not able to reproduce the memory issues ourselves" and needs better insight from the field.
- The existing benchmark crates (`crates/benchmarks`, `project_benchmarks`, `worktree_benchmarks`, `editor_benchmarks`, `fs_benchmarks`) measure wall-clock, not memory, and there is no perf job in CI.
- Recent work like #58999 (periodic memory logging) and #61283 (worktree diagnostics on memory jumps) shows the direction the team is already taking; this initiative extends that same pattern.

I use Zed daily on a 16 GB M1 Pro — a machine where every retained gigabyte matters — so I have both the motivation and a realistic low-RAM environment to test on.

## What I'm proposing

Three small, independent PRs, each additive or opt-in, each with before/after evidence produced by a reproducible external harness:

- [ ] **Child PR 1 — Per-subsystem diagnostics in the existing memory heartbeat.** Extend `reliability.rs`'s significant-change logging with buffer-store counters (retained operations, undo depth), mirroring the `WorktreeStoreDiagnostics` pattern from #61283. Log-only, no behavior change.
- [ ] **Child PR 2 — A bounded sibling for `RetainAllImageCache`.** `RetainAllImageCache` keeps every decoded image for the life of the view; markdown preview and REPL notebooks use it in production. Additive LRU-bounded cache type, opt-in for those two consumers, existing API untouched. Same family as the already-merged #58803 and #29452.
- [ ] **Child PR 3 — Opt-in idle shutdown for language servers.** Default-off setting that stops language servers for worktrees with no visible buffers after a configurable idle window, reusing the existing `stop_language_servers_for_buffers` path. This is the big win for RAM-constrained machines (language servers dominate real-world footprint), and it's the piece I most want feedback on before writing code.

Each child PR will state "Part of this discussion" and link back here; this checklist tracks their status.

## Evidence method

I built [zed-perf-bench](https://github.com/angel-rs/zed-perf-bench) — an external harness that launches a given Zed binary against pinned fixture projects and samples the full process tree (editor + language servers, tagged) at 1 Hz across standard scenarios: cold start, TypeScript monorepo (tsserver), Zed's own repo (rust-analyzer), a 100 MB file, and a long idle soak with linear-regression growth detection. It reports median-of-N with noise flags and produces before/after comparison tables. Every child PR will carry one of those tables, plus host specs.

If the team finds the harness useful beyond these PRs (e.g., as a seed for perf CI), I'm glad to adapt it or donate it — separate conversation, not a condition of any of this.

## How I'd like to work

One PR at a time, smallest first, matching the conventions I see in recent memory work (small diffs, `sysinfo`-based, no new dependencies, opt-in defaults). I've read CONTRIBUTING.md and will sign the CLA with the first PR.

Happy to discuss this initiative if required — and to adjust scope, ordering, or approach to whatever fits the team's direction. If any of the three children is unwanted, I'd rather know here than in review.
