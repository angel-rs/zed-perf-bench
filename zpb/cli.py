"""Command-line entry point for zpb: run, compare, list."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zpb.report import build_scenario_result, render_compare_markdown, utc_timestamp, write_scenario_result
from zpb.scenario import ScenarioRunner, get_zed_version, load_all_scenarios, load_scenario, resolve_zed_binary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zpb", description="Memory/CPU benchmark harness for Zed")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List available scenarios")
    p_list.add_argument("--scenarios-dir", type=Path, default=Path("scenarios"))
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="Run one or more scenarios and write results")
    p_run.add_argument("--zed", type=Path, required=True, help="Path to a zed binary or a .app bundle")
    p_run.add_argument("--label", required=True, help="Label for this batch, e.g. 'before' / 'after'")
    target = p_run.add_mutually_exclusive_group(required=True)
    target.add_argument("--scenario", help="Scenario name to run")
    target.add_argument("--all", action="store_true", help="Run every scenario in --scenarios-dir")
    p_run.add_argument("--runs", type=int, default=3, help="Number of repetitions per scenario (default 3)")
    p_run.add_argument("--scenarios-dir", type=Path, default=Path("scenarios"))
    p_run.add_argument("--fixtures-dir", type=Path, default=Path("fixtures"))
    p_run.add_argument("--results-dir", type=Path, default=Path("results"))
    p_run.add_argument("--sample-interval", type=float, default=1.0, help="Sampler interval in seconds")
    p_run.set_defaults(func=cmd_run)

    p_compare = sub.add_parser("compare", help="Compare two results directories")
    p_compare.add_argument("results_dir_a", type=Path)
    p_compare.add_argument("results_dir_b", type=Path)
    p_compare.add_argument("--out", type=Path, default=None, help="Output markdown path")
    p_compare.add_argument(
        "--results-dir",
        type=Path,
        default=Path("results"),
        help="Where to write compare-<A>-vs-<B>.md if --out is not given",
    )
    p_compare.set_defaults(func=cmd_compare)

    return parser


def cmd_list(args: argparse.Namespace) -> int:
    scenarios = load_all_scenarios(args.scenarios_dir)
    if not scenarios:
        print(f"No scenarios found in {args.scenarios_dir}")
        return 1
    for scenario in scenarios:
        soak = f", soak={scenario.phases.soak_seconds}s" if scenario.phases.soak_seconds else ""
        print(f"{scenario.name}")
        print(f"    {scenario.description}")
        print(f"    settle={scenario.phases.settle_seconds}s{soak}, "
              f"startup_timeout={scenario.phases.startup_timeout}s")
    return 0


def _resolve_scenario(name: str, scenarios_dir: Path):
    direct = scenarios_dir / f"{name}.toml"
    if direct.exists():
        return load_scenario(direct)
    for scenario in load_all_scenarios(scenarios_dir):
        if scenario.name == name:
            return scenario
    raise SystemExit(f"Scenario '{name}' not found in {scenarios_dir}")


def _print_run_summary(result: dict) -> None:
    aggregate = result.get("aggregate")
    if not aggregate:
        print("    all runs failed, no aggregate metrics")
        return
    for metric in ("startup_seconds", "rss_settle_mb", "rss_peak_mb", "cpu_avg_settle_pct",
                   "rss_soak_end_mb", "rss_growth_mb_per_min"):
        if metric not in aggregate:
            continue
        stats = aggregate[metric]
        flag = " [noisy]" if stats["noisy"] else ""
        print(f"    {metric}: median={stats['median']:.2f} stdev={stats['stdev']:.2f}{flag}")


def cmd_run(args: argparse.Namespace) -> int:
    binary = resolve_zed_binary(args.zed)
    version = get_zed_version(binary)
    print(f"Zed binary: {binary}")
    print(f"Zed version: {version}")

    if args.all:
        scenarios = load_all_scenarios(args.scenarios_dir)
        if not scenarios:
            print(f"No scenarios found in {args.scenarios_dir}", file=sys.stderr)
            return 1
    else:
        scenarios = [_resolve_scenario(args.scenario, args.scenarios_dir)]

    runner = ScenarioRunner(binary, args.fixtures_dir, sample_interval=args.sample_interval)
    results_dir = args.results_dir / f"{utc_timestamp()}-{args.label}"

    for scenario in scenarios:
        print(f"\n==> {scenario.name}: {scenario.description}")
        outcomes = []
        for i in range(args.runs):
            print(f"  run {i + 1}/{args.runs} ...", end="", flush=True)
            outcome = runner.run_once(scenario)
            outcomes.append(outcome)
            if outcome.ok:
                print(f" ok (startup={outcome.startup_seconds:.1f}s)")
            else:
                print(f" FAILED: {outcome.error}")
            if outcome.orphans:
                names = [f"{o.pid}:{o.name}({o.tag})" for o in outcome.orphans]
                print(f"    reaped orphans: {names}")

        result = build_scenario_result(scenario, args.label, binary, version, outcomes)
        out_path = results_dir / f"{scenario.name}.json"
        write_scenario_result(out_path, result)
        print(f"  wrote {out_path}")
        _print_run_summary(result)

    print(f"\nResults written to {results_dir}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    label_a = args.results_dir_a.name
    label_b = args.results_dir_b.name
    markdown = render_compare_markdown(args.results_dir_a, args.results_dir_b, label_a, label_b)
    print(markdown)

    out_path = args.out or (args.results_dir / f"compare-{label_a}-vs-{label_b}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown)
    print(f"Written to {out_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
