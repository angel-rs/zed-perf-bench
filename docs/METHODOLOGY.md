# Methodology: why zed-perf-bench measures the way it does

This document justifies the harness design against how the industry measures application memory — Apple, Microsoft, Google/Chromium, Mozilla — and states plainly what we chose, what we rejected, and why. Companion docs: [BASELINE.md](../BASELINE.md) (what a run does, phase by phase) and the README's Limitations section (what we cannot measure yet).

## The five ways the industry measures memory

| Family | Examples | Attribution granularity | Runtime overhead | Code changes in target? | Reproducibility | CI-friendly | Field or lab |
|---|---|---|---|---|---|---|---|
| **(a) External process sampling** | **zed-perf-bench**, Perfetto RSS/PSS counter polling | Process-level only | Near-zero (one syscall per sample) | **No** | High, if measurements are settled/idle-gated | Yes — cheapest to automate | Lab |
| (b) In-process reporters | Firefox `about:memory`, Chromium memory-infra dump providers, mimalloc/jemalloc stats | Subsystem/allocator level | Low-moderate | **Yes** — reporters wired into the app | High, bounded by reporter coverage ("heap-unclassified") | Yes, once reporters exist | Both (Chrome ships some to UMA) |
| (c) Heap profilers | Instruments Allocations/Leaks, heaptrack, dhat, massif, `malloc_history` | Finest — call stack per allocation | High (up to 10-100× for massif/dhat) | No (dynamic hooking) | High per run, too slow to repeat | Poor | Lab |
| (d) Test-framework metrics | Apple `XCTMemoryMetric` + `measure(metrics:)`, BenchmarkDotNet `MemoryDiagnoser` | Whole-test footprint (Apple) or exact allocated bytes (BDN via GC counters) | Low | **Yes** — tests written in the target's framework | Very high (BDN is near-deterministic) | Yes — designed for CI baselines | Lab |
| (e) Field telemetry | MetricKit `MXMemoryMetric`, Chrome UMA, Zed's periodic memory log (#58999) | Coarse (peak/average per session) | Very low | Yes, minimal | Low per sample, high in aggregate | Not a CI gate | Field |

## Where this harness sits, and why

**We chose family (a), deliberately.** The harness exists to produce evidence for upstream PRs from *outside* the project: it must measure stock Zed binaries (stable, preview, or a locally built candidate) **without any code changes in the target** — otherwise every "before" number would require patching the "before" binary, and reviewers could not reproduce results against the release they already ship. Only families (a) and (c) need no target changes, and (c) is 10-100× too slow to run as a repeated, median-of-N scenario suite. That leaves (a): near-zero overhead, works on any binary, automatable.

**The known cost of (a) is attribution.** Process-level sampling says *that* memory grew, not *which subsystem* grew. We mitigate in two ways:

1. **Process-tree tagging.** Zed's architecture externalizes its heaviest consumers into child processes (language servers, Node services). Tagging each child (`zed`, `language-server`, `child-other`) and reporting both aggregate and per-process numbers — Chromium's per-process + summed model — recovers the single most important attribution split for this codebase for free.
2. **Pushing (b) upstream.** The initiative this harness supports proposes in-process diagnostics *inside* Zed (buffer-store counters in the existing memory heartbeat, mirroring `WorktreeStoreDiagnostics`). That is family (b) instrumentation — where it belongs, in the target — reviewed by the people who own the code. The harness proves the case from outside; the PRs improve attribution inside. Family (c) tools (Instruments, dhat, heaptrack) remain the manual deep-dive tier for root-causing whatever the benchmark flags.

**Why not (d)?** `XCTMemoryMetric` and `MemoryDiagnoser` are the right answer *for code you own*. BenchmarkDotNet's exact bytes-per-operation is the cleanest measurement in this survey — and unreachable here: it works because the .NET runtime already tracks every allocation. Rust has no such runtime; the equivalent (allocator stats hooks) requires target changes, i.e. family (b), which is upstream-PR territory, not harness territory.

## Metric choice: phys_footprint first, RSS second

On macOS, RSS double-counts shared pages and misrepresents compressed memory. The number that macOS itself acts on — Jetsam kill decisions, Activity Monitor's "Memory" column, Xcode's memory gauge — is **`phys_footprint`** from `task_info(TASK_VM_INFO)`, approximately: internal (dirty) + compressed + IOKit-mapped + purgeable-nonvolatile + page tables (per Apple's WWDC18 "iOS Memory Deep Dive" definition of footprint as dirty + compressed, and the XNU task ledger). Apple ships a `footprint` CLI explicitly built to show that processed number rather than raw kernel counters; the harness samples it (no sudo required) and reports `footprint_*` as primary on macOS, RSS as the cross-platform secondary. When `footprint` is unavailable, results record the fallback source explicitly — a benchmark that silently swaps metrics is not comparable to itself.

Microsoft's documentation makes the same point on Windows in reverse: WPA distinguishes sharable pages, private pages, process working set, and private working set precisely because "working set" alone misleads. Metric literacy is a design requirement, not a footnote.

## Statistics: median + CV now, change-point detection later

Every aggregated metric reports **median, coefficient of variation, and N**; any metric with CV > 0.10 is flagged `noisy` in the output and in compare tables. This mirrors the gate every serious perf pipeline applies before alerting: Mozilla Perfherder (two-sample t-test plus magnitude threshold), Chromium Pinpoint (A/B repeated "for as many iterations as needed to get a statistically significant result"). With longitudinal history, fixed thresholds should give way to change-point detection (Apache Otava, the E-divisive-means approach from the Hunter paper, arXiv:2301.03034) — on the roadmap, not pretended in v0.

Two honesty rules borrowed from Mozilla: **settled measurement** (AWSY measures after tabs close and GC runs; we measure at a quiesced checkpoint distinct from the whole-window median) and **named instrumentation gaps** (AWSY reports `heap-unclassified` as a first-class metric; our compare output auto-appends a Limitations section listing what we cannot attribute).

One lesson from practice rather than docs: Bruce Dawson's VS Code 64 GB investigation was cracked by anomalous PIDs and handle counts, not by staring at RSS. Raw memory numbers without complementary signals miss whole bug classes — which is why results also record process counts, orphaned children after teardown, and fixture git provenance.

## Noise control

Published methodology for benchmarking GUI apps on macOS is genuinely thin — none of the surveyed vendors documents an end-to-end recipe. Ours is in [BASELINE.md](../BASELINE.md): isolated `--user-data-dir` per run, singleton preflight (refuses to run if any Zed is alive), AC power, no concurrent heavy work, settled checkpoints, median-of-N with noise flags. Known uncontrolled residuals we name rather than hide: Spotlight/`mdworker` indexing bursts, thermal state, first-run OS caches.

## Annotated bibliography

**Apple**
- WWDC21 #10180 "Detect and diagnose memory issues" — the canonical lab→field pipeline: XCTMemoryMetric → Xcode diagnostics → `leaks`/`vmmap`/`heap -diffFrom`/`malloc_history` → MetricKit. https://developer.apple.com/videos/play/wwdc2021/10180/
- WWDC18 #416 "iOS Memory Deep Dive" — clean/dirty/compressed pages; footprint = dirty + compressed. https://developer.apple.com/videos/play/wwdc2018/416/
- WWDC20 #10078 "Why is my app getting killed?" — Jetsam causes. https://developer.apple.com/videos/play/wwdc2020/10078/
- `XCTMemoryMetric` https://developer.apple.com/documentation/xctest/xctmemorymetric · MetricKit `MXMemoryMetric` https://developer.apple.com/documentation/metrickit/mxmemorymetric
- `footprint(1)` man page https://keith.github.io/xcode-man-pages/footprint.1.html · XNU task ledger (phys_footprint) https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/task.c
- "Gathering information about memory use" https://developer.apple.com/documentation/xcode/gathering-information-about-memory-use

**Microsoft**
- WPT memory footprint optimization (working-set taxonomy) https://learn.microsoft.com/en-us/windows-hardware/test/wpt/memory-footprint-optimization
- PerfView (ETW + .NET heap forensics) https://github.com/microsoft/perfview
- BenchmarkDotNet `MemoryDiagnoser` (deterministic bytes/op) https://benchmarkdotnet.org/articles/configs/diagnosers.html
- Bruce Dawson, "Finding a VS Code Memory Leak" (metric literacy in practice) https://randomascii.wordpress.com/2025/10/09/finding-a-vs-code-memory-leak/

**Google/Chromium**
- memory-infra (dump providers, effective_size vs allocated_objects_size) https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/memory-infra/README.md
- Memory benchmarks (`system_health.memory_*`, forced-GC dumps) https://chromium.googlesource.com/chromium/src.git/+/refs/heads/main/docs/memory-infra/memory_benchmarks.md
- Pinpoint bisect https://chromium.googlesource.com/chromium/src/+/HEAD/docs/speed/bisects.md
- Perfetto memory profiling (heapprofd; counter polling) https://perfetto.dev/docs/getting-started/memory-profiling

**Mozilla**
- AWSY ("Are We Slim Yet") — settled measurements, median across processes, explicit/resident-unique/heap-unclassified. https://firefox-source-docs.mozilla.org/performance/memory/awsy.html
- nnethercote, DMD / cumulative heap profiling (the "dark matter" concept) https://blog.mozilla.org/nnethercote/2014/12/11/cumulative-heap-profiling-in-firefox-with-dmd/
- Rust Performance Book, profiling chapter (dhat, heaptrack, bytehound) https://nnethercote.github.io/perf-book/profiling.html

**Statistics**
- Hunter / E-divisive change-point detection (DataStax → Apache Otava) https://arxiv.org/pdf/2301.03034 · https://otava.apache.org/docs/math/
- MongoDB: change-point detection + human triage in CI https://www.mongodb.com/company/blog/using-change-point-detection-find-performance-regressions

**Concepts**
- Brendan Gregg, *Systems Performance*, memory chapter + USE method (utilization/saturation/errors as a field lens) https://www.brendangregg.com/sysperfbook.html
