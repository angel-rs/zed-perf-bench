"""Scenario loading and the run pipeline (launch -> startup detection ->
settle -> optional soak -> teardown) for a single Zed process.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from zpb.sampler import ProcessTreeSampler, Sample, classify_tag

DEFAULT_SETTLE_SECONDS = 60
DEFAULT_SOAK_SECONDS = 0
DEFAULT_STARTUP_TIMEOUT = 120

# Startup heuristic: the process tree is considered "settled" once its
# combined CPU usage stays below this threshold for STARTUP_STABLE_SAMPLES
# consecutive 1s samples. This is a heuristic, not window instrumentation —
# see README "Methodology" for why.
STARTUP_CPU_THRESHOLD_PCT = 10.0
STARTUP_STABLE_SAMPLES = 3

TEARDOWN_SIGTERM_WAIT_SECONDS = 10


@dataclass
class Phases:
    settle_seconds: int = DEFAULT_SETTLE_SECONDS
    soak_seconds: int = DEFAULT_SOAK_SECONDS
    startup_timeout: int = DEFAULT_STARTUP_TIMEOUT


@dataclass
class Scenario:
    name: str
    description: str
    project_path: str  # relative to fixtures/, "" = no project
    zed_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    phases: Phases = field(default_factory=Phases)
    source_path: Path | None = None

    def resolve_project_path(self, fixtures_dir: Path) -> Path | None:
        if not self.project_path:
            return None
        return fixtures_dir / self.project_path


def load_scenario(path: Path) -> Scenario:
    with path.open("rb") as f:
        data = tomllib.load(f)

    phases_data = data.get("phases", {})
    phases = Phases(
        settle_seconds=phases_data.get("settle_seconds", DEFAULT_SETTLE_SECONDS),
        soak_seconds=phases_data.get("soak_seconds", DEFAULT_SOAK_SECONDS),
        startup_timeout=phases_data.get("startup_timeout", DEFAULT_STARTUP_TIMEOUT),
    )
    return Scenario(
        name=data["name"],
        description=data.get("description", ""),
        project_path=data.get("project_path", ""),
        zed_args=list(data.get("zed_args", [])),
        env=dict(data.get("env", {})),
        phases=phases,
        source_path=path,
    )


def load_all_scenarios(scenarios_dir: Path) -> list[Scenario]:
    scenarios = [load_scenario(p) for p in sorted(scenarios_dir.glob("*.toml"))]
    return sorted(scenarios, key=lambda s: s.name)


def resolve_zed_binary(zed_path: Path) -> Path:
    """Resolve a path to either a Zed binary or a .app bundle into the
    actual executable path.
    """
    if zed_path.suffix == ".app":
        binary = zed_path / "Contents" / "MacOS" / "zed"
    else:
        binary = zed_path
    if not binary.exists():
        raise FileNotFoundError(f"Zed binary not found at resolved path: {binary}")
    return binary


def get_zed_version(binary: Path) -> str:
    """Best-effort Zed version capture.

    `--version` is the documented flag for Zed's installed CLI, but the raw
    app-bundle binary at `Contents/MacOS/zed` (what this harness launches
    directly, per design — see resolve_zed_binary) does not implement it on
    every build; it exposes `--system-specs` instead, whose first line is
    "Zed: v<version> (...)". Try `--version` first for forward/cross-platform
    compatibility, then fall back to `--system-specs`.
    """
    try:
        result = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=15, check=False
        )
        if result.returncode == 0:
            out = result.stdout.strip() or result.stderr.strip()
            if out:
                return out
    except (subprocess.SubprocessError, OSError):
        pass

    try:
        result = subprocess.run(
            [str(binary), "--system-specs"], capture_output=True, text=True, timeout=15, check=False
        )
        for line in result.stdout.splitlines():
            if line.strip().lower().startswith("zed:"):
                return line.strip()
        if result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
    except (subprocess.SubprocessError, OSError) as exc:
        return f"unknown ({exc})"

    return "unknown"


@dataclass
class PhaseBoundaries:
    startup_end_t: float
    settle_end_t: float
    soak_end_t: float


@dataclass
class Orphan:
    pid: int
    name: str
    tag: str


class RunAbortedError(RuntimeError):
    """Raised when a run cannot proceed. Caught by run_once and converted
    into a failed RunOutcome rather than propagating."""


class StartupTimeoutError(RunAbortedError):
    pass


class SingletonConflictError(RunAbortedError):
    pass


@dataclass
class RunOutcome:
    ok: bool
    error: str | None
    samples: list[Sample]
    phases: PhaseBoundaries | None
    startup_seconds: float | None
    orphans: list[Orphan]


class ScenarioRunner:
    """Runs a single scenario against a resolved Zed binary, once per call
    to `run_once`. Owns process spawn, sampling, and teardown/PID hygiene.
    """

    def __init__(self, zed_binary: Path, fixtures_dir: Path, sample_interval: float = 1.0) -> None:
        self.zed_binary = zed_binary
        self.fixtures_dir = fixtures_dir
        self.sample_interval = sample_interval

    def run_once(self, scenario: Scenario) -> RunOutcome:
        sampler: ProcessTreeSampler | None = None
        proc: subprocess.Popen | None = None
        user_data_dir: Path | None = None
        try:
            self._check_no_existing_zed_instance()

            project_path = scenario.resolve_project_path(self.fixtures_dir)
            user_data_dir = Path(tempfile.mkdtemp(prefix="zpb-userdata-"))

            env = os.environ.copy()
            env.update(scenario.env)

            args = [str(self.zed_binary), "--user-data-dir", str(user_data_dir)]
            if project_path is not None:
                args.append(str(project_path))
            args.extend(scenario.zed_args)

            proc = subprocess.Popen(args, env=env)
            sampler = ProcessTreeSampler(proc.pid, interval=self.sample_interval)
            sampler.start()

            startup_seconds = self._wait_for_settle(sampler, proc, scenario.phases.startup_timeout)
            startup_end_t = sampler.snapshot()[-1].t

            time.sleep(scenario.phases.settle_seconds)
            settle_end_t = sampler.snapshot()[-1].t

            if scenario.phases.soak_seconds > 0:
                time.sleep(scenario.phases.soak_seconds)
            soak_end_t = sampler.snapshot()[-1].t

            phases = PhaseBoundaries(
                startup_end_t=startup_end_t, settle_end_t=settle_end_t, soak_end_t=soak_end_t
            )
            samples = sampler.snapshot()
            orphans = self._teardown(proc)
            return RunOutcome(
                ok=True,
                error=None,
                samples=samples,
                phases=phases,
                startup_seconds=startup_seconds,
                orphans=orphans,
            )
        except RunAbortedError as exc:
            samples = sampler.snapshot() if sampler else []
            orphans = self._teardown(proc) if proc else []
            return RunOutcome(
                ok=False, error=str(exc), samples=samples, phases=None, startup_seconds=None, orphans=orphans
            )
        finally:
            if sampler is not None:
                sampler.stop()
            if user_data_dir is not None:
                shutil.rmtree(user_data_dir, ignore_errors=True)

    def _check_no_existing_zed_instance(self) -> None:
        """Zed is a singleton GUI application: launching it while another
        instance is already running does not spawn an independent,
        measurable process. Depending on the arguments, it either no-ops
        (empty project_path) or hands the open request off to the already
        running instance — potentially opening a window inside a real,
        already-in-use Zed session instead of the isolated instance this
        harness expects to measure and tear down. Fail loudly before that
        can happen rather than silently recording a bogus measurement.
        """
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"] == "zed":
                    raise SingletonConflictError(
                        f"Another Zed process is already running (pid={proc.info['pid']}). "
                        "Zed is a singleton application: quit every Zed window/instance "
                        "before running zpb, otherwise this run will not measure an "
                        "isolated process."
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _wait_for_settle(
        self, sampler: ProcessTreeSampler, proc: subprocess.Popen, timeout: int
    ) -> float:
        deadline = time.monotonic() + timeout
        while True:
            if proc.poll() is not None:
                raise StartupTimeoutError(
                    f"Zed process exited during startup (returncode={proc.returncode})"
                )
            samples = sampler.snapshot()
            if len(samples) >= STARTUP_STABLE_SAMPLES:
                recent = samples[-STARTUP_STABLE_SAMPLES:]
                if all(s.tree_cpu_percent < STARTUP_CPU_THRESHOLD_PCT for s in recent):
                    return recent[-1].t
            if time.monotonic() > deadline:
                raise StartupTimeoutError(
                    f"Zed did not settle (tree CPU < {STARTUP_CPU_THRESHOLD_PCT}% for "
                    f"{STARTUP_STABLE_SAMPLES}s) within startup_timeout={timeout}s"
                )
            time.sleep(0.2)

    def _teardown(self, proc: subprocess.Popen | None) -> list[Orphan]:
        """Kill the main process and any surviving children. No process
        may survive the harness — this is a hard requirement, not a
        best-effort cleanup.
        """
        if proc is None:
            return []

        children: list[psutil.Process] = []
        try:
            children = psutil.Process(proc.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            pass

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=TEARDOWN_SIGTERM_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=TEARDOWN_SIGTERM_WAIT_SECONDS)

        orphans: list[Orphan] = []
        for child in children:
            try:
                if not child.is_running():
                    continue
                name = child.name()
                cmdline: list[str] = []
                try:
                    cmdline = child.cmdline()
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    pass
                tag = classify_tag(name, cmdline)
                orphans.append(Orphan(pid=child.pid, name=name, tag=tag))
                child.send_signal(signal.SIGKILL)
            except psutil.NoSuchProcess:
                continue

        # Give SIGKILL a moment to land, then verify. This is the PID
        # hygiene guarantee: if anything is still alive here, something is
        # wrong with the harness itself and it should be loud about it.
        if orphans:
            time.sleep(0.5)
            for orphan in orphans:
                if psutil.pid_exists(orphan.pid):
                    raise RuntimeError(
                        f"Failed to reap orphaned process pid={orphan.pid} name={orphan.name!r} "
                        "after SIGKILL — harness cannot guarantee clean teardown"
                    )

        return orphans
