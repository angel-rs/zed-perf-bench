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

from zpb.sampler import Sample
from zpb.scenario import Orphan, PhaseBoundaries, RunOutcome, Scenario

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


def compute_run_metrics(
    samples: list[Sample], phases: PhaseBoundaries, startup_seconds: float
) -> dict[str, Any]:
    settle_samples = _samples_in_window(samples, phases.startup_end_t, phases.settle_end_t)
    if not settle_samples:
        settle_samples = samples[-1:]

    rss_settle_mb = statistics.median(s.tree_rss_bytes for s in settle_samples) / BYTES_PER_MB
    rss_peak_mb = max(s.tree_rss_bytes for s in samples) / BYTES_PER_MB
    cpu_avg_settle_pct = statistics.mean(s.tree_cpu_percent for s in settle_samples)

    tags = {reading.tag for s in settle_samples for reading in s.processes}
    rss_settle_by_tag_mb = {}
    for tag in sorted(tags):
        per_sample = [
            sum(r.rss_bytes for r in s.processes if r.tag == tag) for s in settle_samples
        ]
        rss_settle_by_tag_mb[tag] = statistics.median(per_sample) / BYTES_PER_MB

    metrics: dict[str, Any] = {
        "startup_seconds": startup_seconds,
        "rss_settle_mb": rss_settle_mb,
        "rss_peak_mb": rss_peak_mb,
        "cpu_avg_settle_pct": cpu_avg_settle_pct,
        "rss_settle_by_tag_mb": rss_settle_by_tag_mb,
    }

    if phases.soak_end_t > phases.settle_end_t:
        soak_samples = _samples_in_window(samples, phases.settle_end_t, phases.soak_end_t)
        if soak_samples:
            tail = soak_samples[-SOAK_TAIL_SAMPLES:]
            metrics["rss_soak_end_mb"] = (
                statistics.median(s.tree_rss_bytes for s in tail) / BYTES_PER_MB
            )
            xs_minutes = [s.t / 60.0 for s in soak_samples]
            ys_mb = [s.tree_rss_bytes / BYTES_PER_MB for s in soak_samples]
            metrics["rss_growth_mb_per_min"] = _linear_regression_slope(xs_minutes, ys_mb)

    return metrics


def _aggregate_leaf(values: list[float]) -> dict[str, Any]:
    med = statistics.median(values)
    stdev = statistics.stdev(values) if len(values) >= 2 else 0.0
    noisy = (stdev / med > NOISY_THRESHOLD) if med != 0 else stdev > 0
    return {"median": med, "stdev": stdev, "noisy": noisy, "n": len(values)}


def aggregate_metrics(run_metrics_list: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Aggregate per-run metrics dicts (as produced by compute_run_metrics)
    into median/stdev/noisy per metric. Nested dicts (per-tag breakdowns)
    are aggregated per-key. Returns None if there is nothing to aggregate.
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
) -> dict[str, Any]:
    runs = [run_outcome_to_dict(i, o) for i, o in enumerate(outcomes)]
    ok_metrics = [r["metrics"] for r in runs if r["ok"] and r["metrics"] is not None]
    return {
        "scenario": scenario.name,
        "description": scenario.description,
        "label": label,
        "timestamp_utc": utc_iso_now(),
        "zed_binary": str(zed_binary),
        "zed_version": zed_version,
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


def _flatten_aggregate(aggregate: dict[str, Any] | None) -> dict[str, float]:
    """Flatten an aggregate dict (as produced by aggregate_metrics) into
    {metric_name: median}, expanding per-tag breakdowns as "metric[tag]".
    """
    flat: dict[str, float] = {}
    if not aggregate:
        return flat
    for key, value in aggregate.items():
        if "median" in value:
            flat[key] = value["median"]
        else:
            for subkey, subvalue in value.items():
                flat[f"{key}[{subkey}]"] = subvalue["median"]
    return flat


def render_compare_markdown(dir_a: Path, dir_b: Path, label_a: str, label_b: str) -> str:
    files_a = {p.name: p for p in dir_a.glob("*.json")}
    files_b = {p.name: p for p in dir_b.glob("*.json")}
    common = sorted(set(files_a) & set(files_b))

    lines = [f"# Compare: {label_a} vs {label_b}", ""]
    if not common:
        lines.append(f"No matching scenario result files found in both `{dir_a}` and `{dir_b}`.")
        return "\n".join(lines) + "\n"

    for name in common:
        result_a = load_scenario_result(files_a[name])
        result_b = load_scenario_result(files_b[name])
        scenario_name = result_a.get("scenario", name)
        lines.append(f"## {scenario_name}")
        lines.append("")
        lines.append(f"| metric | {label_a} | {label_b} | Δ | Δ% |")
        lines.append("|---|---|---|---|---|")

        flat_a = _flatten_aggregate(result_a.get("aggregate"))
        flat_b = _flatten_aggregate(result_b.get("aggregate"))
        for metric in sorted(set(flat_a) | set(flat_b)):
            val_a = flat_a.get(metric)
            val_b = flat_b.get(metric)
            if val_a is None or val_b is None:
                lines.append(f"| {metric} | {val_a if val_a is not None else 'n/a'} | "
                             f"{val_b if val_b is not None else 'n/a'} | n/a | n/a |")
                continue
            delta = val_b - val_a
            delta_pct = (delta / val_a * 100) if val_a != 0 else float("nan")
            delta_str = f"{delta:+.2f}"
            if _is_memory_metric(metric) and delta != 0:
                delta_str += " (improvement)" if delta < 0 else " (regression)"
            delta_pct_str = "n/a" if delta_pct != delta_pct else f"{delta_pct:+.1f}%"  # NaN check
            lines.append(f"| {metric} | {val_a:.2f} | {val_b:.2f} | {delta_str} | {delta_pct_str} |")
        lines.append("")

    return "\n".join(lines) + "\n"
