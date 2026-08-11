"""Metrics computation, JSON result I/O, and markdown compare tables.

All "*_mb" fields are mebibytes (bytes / 1024**2), not decimal megabytes.
"""

from __future__ import annotations

import json
import platform
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from zpb import harness_version
from zpb.sampler import Sample, footprint_available
from zpb.scenario import Orphan, PhaseBoundaries, RunOutcome, Scenario, git_fixture_provenance

BYTES_PER_MB = 1024 * 1024
NOISY_THRESHOLD = 0.10
SOAK_TAIL_SAMPLES = 5


def utc_timestamp() -> str:
    """UTC timestamp suitable for a results directory name, e.g. 20260811T060000Z."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def host_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "physical_ram_mb": round(psutil.virtual_memory().total / BYTES_PER_MB, 1),
        "footprint_source": (
            "phys_footprint (/usr/bin/footprint)" if footprint_available() else "unavailable (RSS only)"
        ),
    }
    mac_ver = platform.mac_ver()[0]
    if mac_ver:
        info["macos_version"] = mac_ver
    return info


def _samples_in_window(samples: list[Sample], start_t: float, end_t: float) -> list[Sample]:
    return [s for s in samples if start_t <= s.t <= end_t]


def _linear_regression_slope(xs: list[float], ys: list[float]) -> float:
    """Least-squares slope of ys against xs. Returns 0.0 if underdetermined
    or if x has no spread (avoids a ZeroDivisionError on a flat window).
    """
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _tree_values(samples: list[Sample], value_fn) -> list[float]:
    """value_fn(sample) for each sample, dropping the Nones (e.g. a
    tree_footprint_bytes that was unobtainable for that tick)."""
    return [v for v in (value_fn(s) for s in samples) if v is not None]


def _tag_breakdown_mb(samples: list[Sample], value_fn) -> dict[str, float]:
    """Median per-tag sum of value_fn(reading) (bytes) across samples, in
    MB. Readings where value_fn returns None are excluded from that
    sample's tag sum (relevant for footprint_bytes, which can be
    unobtainable per-process; a no-op for rss_bytes, which never is).
    """
    tags = {reading.tag for s in samples for reading in s.processes}
    breakdown: dict[str, float] = {}
    for tag in sorted(tags):
        per_sample = []
        for s in samples:
            values = [v for v in (value_fn(r) for r in s.processes if r.tag == tag) if v is not None]
            if values:
                per_sample.append(sum(values))
        if per_sample:
            breakdown[tag] = statistics.median(per_sample) / BYTES_PER_MB
    return breakdown


def compute_run_metrics(
    samples: list[Sample], phases: PhaseBoundaries, startup_seconds: float
) -> dict[str, Any]:
    settle_samples = _samples_in_window(samples, phases.startup_end_t, phases.settle_end_t)
    if not settle_samples:
        settle_samples = samples[-1:]

    rss_settle_mb = statistics.median(s.tree_rss_bytes for s in settle_samples) / BYTES_PER_MB
    rss_peak_mb = max(s.tree_rss_bytes for s in samples) / BYTES_PER_MB
    cpu_avg_settle_pct = statistics.mean(s.tree_cpu_percent for s in settle_samples)
    rss_settle_by_tag_mb = _tag_breakdown_mb(settle_samples, lambda r: r.rss_bytes)

    metrics: dict[str, Any] = {
        "startup_seconds": startup_seconds,
        "rss_settle_mb": rss_settle_mb,
        "rss_peak_mb": rss_peak_mb,
        "cpu_avg_settle_pct": cpu_avg_settle_pct,
        "rss_settle_by_tag_mb": rss_settle_by_tag_mb,
    }

    # phys_footprint is the primary memory metric when obtainable (see
    # zpb/sampler.py); it mirrors every rss_* metric above under a
    # footprint_* name, but only when at least one sample in the relevant
    # window actually has it — on a host where `footprint` is unavailable,
    # these keys are simply absent and RSS remains the only signal (see
    # README "Limitations").
    footprint_settle_values = _tree_values(settle_samples, lambda s: s.tree_footprint_bytes)
    if footprint_settle_values:
        metrics["footprint_settle_mb"] = statistics.median(footprint_settle_values) / BYTES_PER_MB
        footprint_peak_values = _tree_values(samples, lambda s: s.tree_footprint_bytes)
        if footprint_peak_values:
            metrics["footprint_peak_mb"] = max(footprint_peak_values) / BYTES_PER_MB
        footprint_settle_by_tag_mb = _tag_breakdown_mb(settle_samples, lambda r: r.footprint_bytes)
        if footprint_settle_by_tag_mb:
            metrics["footprint_settle_by_tag_mb"] = footprint_settle_by_tag_mb

    # Quiesce checkpoint (AWSY-style "settled" measurement): median over the
    # short post-settle quiesce window only, distinct from rss_settle_mb /
    # footprint_settle_mb above, which cover the whole settle window.
    quiesce_samples = _samples_in_window(samples, phases.settle_end_t, phases.quiesce_end_t)
    if quiesce_samples:
        metrics["rss_settled_mb"] = statistics.median(s.tree_rss_bytes for s in quiesce_samples) / BYTES_PER_MB
        footprint_quiesce_values = _tree_values(quiesce_samples, lambda s: s.tree_footprint_bytes)
        if footprint_quiesce_values:
            metrics["footprint_settled_mb"] = statistics.median(footprint_quiesce_values) / BYTES_PER_MB

    if phases.soak_end_t > phases.quiesce_end_t:
        soak_samples = _samples_in_window(samples, phases.quiesce_end_t, phases.soak_end_t)
        if soak_samples:
            tail = soak_samples[-SOAK_TAIL_SAMPLES:]
            metrics["rss_soak_end_mb"] = (
                statistics.median(s.tree_rss_bytes for s in tail) / BYTES_PER_MB
            )
            xs_minutes = [s.t / 60.0 for s in soak_samples]
            ys_mb = [s.tree_rss_bytes / BYTES_PER_MB for s in soak_samples]
            metrics["rss_growth_mb_per_min"] = _linear_regression_slope(xs_minutes, ys_mb)

            footprint_tail_values = _tree_values(tail, lambda s: s.tree_footprint_bytes)
            if footprint_tail_values:
                metrics["footprint_soak_end_mb"] = statistics.median(footprint_tail_values) / BYTES_PER_MB
            footprint_soak_points = [
                (s.t / 60.0, s.tree_footprint_bytes / BYTES_PER_MB)
                for s in soak_samples
                if s.tree_footprint_bytes is not None
            ]
            if len(footprint_soak_points) >= 2:
                fp_xs = [x for x, _ in footprint_soak_points]
                fp_ys = [y for _, y in footprint_soak_points]
                metrics["footprint_growth_mb_per_min"] = _linear_regression_slope(fp_xs, fp_ys)

    return metrics


def _aggregate_leaf(values: list[float]) -> dict[str, Any]:
    med = statistics.median(values)
    stdev = statistics.stdev(values) if len(values) >= 2 else 0.0
    if med != 0:
        cv = stdev / med
        noisy = cv > NOISY_THRESHOLD
    else:
        # CV is undefined at a zero median (division by zero); fall back to
        # "any spread at all counts as noisy" rather than emit inf/NaN into
        # result JSON.
        cv = None
        noisy = stdev > 0
    return {"median": med, "stdev": stdev, "cv": cv, "noisy": noisy, "n": len(values)}


def aggregate_metrics(run_metrics_list: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Aggregate per-run metrics dicts (as produced by compute_run_metrics)
    into median/stdev/cv/noisy/n per metric (Perfherder/Pinpoint
    convention: report enough for a reviewer to judge signal vs noise, not
    just the point estimate). Nested dicts (per-tag breakdowns) are
    aggregated per-key. Returns None if there is nothing to aggregate.
    """
    if not run_metrics_list:
        return None

    keys = set()
    for m in run_metrics_list:
        keys.update(m.keys())

    aggregate: dict[str, Any] = {}
    for key in sorted(keys):
        sample_values = [m[key] for m in run_metrics_list if key in m]
        if not sample_values:
            continue
        if isinstance(sample_values[0], dict):
            subkeys = set()
            for v in sample_values:
                subkeys.update(v.keys())
            aggregate[key] = {
                subkey: _aggregate_leaf([v[subkey] for v in sample_values if subkey in v])
                for subkey in sorted(subkeys)
                if [v[subkey] for v in sample_values if subkey in v]
            }
        else:
            aggregate[key] = _aggregate_leaf(sample_values)
    return aggregate


def sample_to_dict(sample: Sample) -> dict[str, Any]:
    return asdict(sample)


def run_outcome_to_dict(run_index: int, outcome: RunOutcome) -> dict[str, Any]:
    metrics = None
    if outcome.ok and outcome.phases is not None:
        metrics = compute_run_metrics(outcome.samples, outcome.phases, outcome.startup_seconds)
    return {
        "run_index": run_index,
        "ok": outcome.ok,
        "error": outcome.error,
        "phases": asdict(outcome.phases) if outcome.phases else None,
        "orphans": [asdict(o) for o in outcome.orphans],
        "metrics": metrics,
        "samples": [sample_to_dict(s) for s in outcome.samples],
    }


def build_scenario_result(
    scenario: Scenario,
    label: str,
    zed_binary: Path,
    zed_version: str,
    outcomes: list[RunOutcome],
    fixtures_dir: Path,
) -> dict[str, Any]:
    runs = [run_outcome_to_dict(i, o) for i, o in enumerate(outcomes)]
    ok_metrics = [r["metrics"] for r in runs if r["ok"] and r["metrics"] is not None]
    # Fixture provenance (fixture_git_sha / fixture_dirty): recorded so a
    # run against a locally-linked checkout (fixtures/fetch.sh --link-zed)
    # is as auditable as one against a pinned-SHA clone. See
    # zpb.scenario.git_fixture_provenance for the graceful-failure cases.
    provenance = git_fixture_provenance(scenario.resolve_project_path(fixtures_dir))
    return {
        "harness_version": harness_version,
        "scenario": scenario.name,
        "description": scenario.description,
        "label": label,
        "timestamp_utc": utc_iso_now(),
        "zed_binary": str(zed_binary),
        "zed_version": zed_version,
        "fixture_git_sha": provenance["fixture_git_sha"],
        "fixture_dirty": provenance["fixture_dirty"],
        "host": host_info(),
        "runs": runs,
        "aggregate": aggregate_metrics(ok_metrics),
    }


def write_scenario_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")


def load_scenario_result(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


# --- compare -----------------------------------------------------------

MEMORY_METRIC_MARKERS = ("rss", "mb")


def _is_memory_metric(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in MEMORY_METRIC_MARKERS)


def _flatten_aggregate(aggregate: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Flatten an aggregate dict (as produced by aggregate_metrics) into
    {metric_name: leaf}, expanding per-tag breakdowns as "metric[tag]".
    Each leaf is the {median, stdev, cv, noisy, n} dict from _aggregate_leaf.
    """
    flat: dict[str, dict[str, Any]] = {}
    if not aggregate:
        return flat
    for key, value in aggregate.items():
        if "median" in value:
            flat[key] = value
        else:
            for subkey, subvalue in value.items():
                flat[f"{key}[{subkey}]"] = subvalue
    return flat


def _format_cv(leaf: dict[str, Any] | None) -> str:
    if leaf is None or leaf["cv"] is None:
        return "n/a"
    return f"{leaf['cv']:.3f}" + (" [noisy]" if leaf["noisy"] else "")


def _limitations_lines(footprint_sources: set[str]) -> list[str]:
    """The gaps this harness cannot currently see through, in the spirit of
    Firefox/Chromium naming their own instrumentation gaps (e.g.
    "heap-unclassified") instead of implying false precision.
    """
    if len(footprint_sources) > 1:
        footprint_line = (
            "- **Mixed footprint source across these results**: "
            + "; ".join(sorted(footprint_sources))
            + " — the two sides of this comparison were not captured with the same memory source, "
            "which is itself a confound. Prefer re-running both labels on the same host."
        )
    elif footprint_sources:
        source = next(iter(footprint_sources))
        footprint_line = f"- **Footprint source for these results**: {source}."
    else:
        footprint_line = "- **Footprint source for these results**: unknown (no host info in the result JSON)."

    return [
        "## Limitations",
        "",
        "- **No USS/PSS.** `psutil` cannot report unique or proportional set size on macOS, only RSS "
        "(and, when available, phys_footprint). RSS double-counts pages shared between the editor and "
        "its language servers — a real confound for a multi-process tree like this one.",
        "- **No allocator-level or per-subsystem breakdown.** This harness sees whole-process totals only; "
        "it cannot attribute growth to a specific Zed subsystem, buffer, or heap category the way an "
        "in-process allocator hook could (Firefox/Chromium call this class of gap `heap-unclassified` "
        "rather than pretend it doesn't exist — same idea here).",
        "- **RSS is not Jetsam-equivalent.** macOS's memory-pressure killer and Activity Monitor act on "
        "`phys_footprint`, not RSS; RSS does not reflect compressed memory and over-counts shared pages "
        "relative to it.",
        footprint_line,
        "- **Settings aren't isolated.** Only `--user-data-dir` is per-run; `settings.json`/`keymap.json` "
        "are read from the normal Zed config location on both sides of a comparison (see README "
        "Methodology → Config isolation).",
        "",
    ]


def render_compare_markdown(dir_a: Path, dir_b: Path, label_a: str, label_b: str) -> str:
    files_a = {p.name: p for p in dir_a.glob("*.json")}
    files_b = {p.name: p for p in dir_b.glob("*.json")}
    common = sorted(set(files_a) & set(files_b))

    lines = [f"# Compare: {label_a} vs {label_b}", ""]
    if not common:
        lines.append(f"No matching scenario result files found in both `{dir_a}` and `{dir_b}`.")
        return "\n".join(lines) + "\n"

    footprint_sources: set[str] = set()

    for name in common:
        result_a = load_scenario_result(files_a[name])
        result_b = load_scenario_result(files_b[name])
        version_a = result_a.get("harness_version", "unknown (pre-0.2.0)")
        version_b = result_b.get("harness_version", "unknown (pre-0.2.0)")
        for result in (result_a, result_b):
            source = result.get("host", {}).get("footprint_source")
            if source:
                footprint_sources.add(source)

        scenario_name = result_a.get("scenario", name)
        lines.append(f"## {scenario_name}")
        lines.append("")
        if version_a != version_b:
            lines.append(
                f"> **WARNING:** comparing `harness_version` {version_a} ({label_a}) against "
                f"{version_b} ({label_b}) — metric shapes and methodology may differ between them; "
                "treat this comparison as indicative only."
            )
            lines.append("")
        lines.append(f"| metric | {label_a} | {label_b} | Δ | Δ% | N (a/b) | CV (a/b) |")
        lines.append("|---|---|---|---|---|---|---|")

        flat_a = _flatten_aggregate(result_a.get("aggregate"))
        flat_b = _flatten_aggregate(result_b.get("aggregate"))
        for metric in sorted(set(flat_a) | set(flat_b)):
            leaf_a = flat_a.get(metric)
            leaf_b = flat_b.get(metric)
            val_a = leaf_a["median"] if leaf_a else None
            val_b = leaf_b["median"] if leaf_b else None
            n_str = f"{leaf_a['n'] if leaf_a else 'n/a'}/{leaf_b['n'] if leaf_b else 'n/a'}"
            cv_str = f"{_format_cv(leaf_a)}/{_format_cv(leaf_b)}"
            if val_a is None or val_b is None:
                lines.append(f"| {metric} | {val_a if val_a is not None else 'n/a'} | "
                             f"{val_b if val_b is not None else 'n/a'} | n/a | n/a | {n_str} | {cv_str} |")
                continue
            delta = val_b - val_a
            delta_pct = (delta / val_a * 100) if val_a != 0 else float("nan")
            delta_str = f"{delta:+.2f}"
            if _is_memory_metric(metric) and delta != 0:
                delta_str += " (improvement)" if delta < 0 else " (regression)"
            delta_pct_str = "n/a" if delta_pct != delta_pct else f"{delta_pct:+.1f}%"  # NaN check
            lines.append(
                f"| {metric} | {val_a:.2f} | {val_b:.2f} | {delta_str} | {delta_pct_str} | "
                f"{n_str} | {cv_str} |"
            )
        lines.append("")

    lines.extend(_limitations_lines(footprint_sources))

    return "\n".join(lines) + "\n"
