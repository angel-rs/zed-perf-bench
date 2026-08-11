# [DRAFT — child PR 2 of 3]

**Title:** gpui: Add a bounded image cache and use it in markdown preview and notebooks

**Branch (on angel-rs/zed):** `memory-observability/bounded-image-cache`

---

Part of the memory observability initiative discussed in #PARENT (child PR 2 of 3, follows #CHILD1).

## Problem

`RetainAllImageCache` (`crates/gpui/src/elements/image_cache.rs`) keeps every decoded image alive until the view is dropped or the cache is manually cleared. Markdown preview (`crates/markdown_preview/src/markdown_preview_view.rs`) and REPL notebook cells use it in production, so scrolling through an image-heavy document accumulates every decoded image for the life of the view. Same family as the texture/image leaks fixed in #58803 and #29452.

## Change

- Add `BoundedImageCache`: same interface, LRU eviction above a configurable entry budget. Purely additive — `RetainAllImageCache` is untouched and remains the default everywhere else.
- Switch the two known unbounded production consumers (markdown preview, notebook cells) to it with a conservative budget.

## Evidence

Harness: [zed-perf-bench](https://github.com/angel-rs/zed-perf-bench). A gpui example loads N unique images through both cache types; `zpb compare` before/after shows retained footprint flat vs. linear in N. Median of 3 runs, host specs attached.

<!-- paste zpb compare table + example output here before opening -->

## Release Notes:

- Fixed unbounded image memory growth in markdown preview and notebook cells.
