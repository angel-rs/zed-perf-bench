"""Process-tree sampler.

Samples a root process and all of its descendants at a fixed interval
(default 1 Hz), recording per-process RSS and CPU usage plus a coarse
"tag" derived from the process name/cmdline (e.g. "language-server").

On macOS, each sample also attempts to record `phys_footprint` — the
figure Jetsam and Activity Monitor actually act on, unlike RSS, which
double-counts shared pages and does not reflect compressed memory. See
`footprint_available()` / `sample_footprints()` below for how, and the
README "Methodology" section for the empirical case against RSS-only.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import psutil

# Substrings matched (case-insensitively) against "<name> <cmdline>" to
# classify a child process as a language server. This list is deliberately
# a set of known binary/module names rather than a heuristic on CPU/RSS.
KNOWN_LANGUAGE_SERVERS: tuple[str, ...] = (
    "rust-analyzer",
    "typescript-language-server",
    "vtsls",
    "tsserver",
    "gopls",
    "pyright",
    "clangd",
)

TAG_ZED = "zed"
TAG_LANGUAGE_SERVER = "language-server"
TAG_CHILD_OTHER = "child-other"

# macOS's `footprint` CLI (part of the OS, no install needed) reports
# `phys_footprint` for a target pid — the same figure `task_info`'s
# TASK_VM_INFO exposes to Jetsam and Activity Monitor. Verified empirically
# on this machine (macOS 26.5.2 / Darwin 25.5, see README Methodology):
# unprivileged, unsandboxed invocation against a same-user process works
# and returns clean JSON via `-j`. The alternatives don't: raw ctypes
# `task_info(TASK_VM_INFO)` needs `task_for_pid` entitlements this harness
# doesn't have and fails against any process that isn't itself; `top -pid`
# reports a "MEM" column derived from resident size, not phys_footprint,
# so it doesn't actually solve the RSS-double-counts-shared-pages problem.
FOOTPRINT_BIN = Path("/usr/bin/footprint")
FOOTPRINT_TIMEOUT_SECONDS = 5.0


@lru_cache(maxsize=1)
def footprint_available() -> bool:
    """Whether `sample_footprints()` can be expected to work on this host."""
    return platform.system() == "Darwin" and os.access(FOOTPRINT_BIN, os.X_OK)


def sample_footprints(pids: list[int]) -> dict[int, int]:
    """Best-effort phys_footprint (bytes) for each of `pids`, via one
    batched `footprint` invocation covering the whole list.

    `footprint` tolerates a partially-missing pid list (exit code 0, JSON
    covers the survivors) and only fails outright (exit code 66, no JSON
    file written) when none of them exist any more — both are treated as
    "return whatever data is available," not an error, since a process in
    the tree exiting mid-sample is an expected race, not a harness bug.
    """
    if not pids:
        return {}
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [
                str(FOOTPRINT_BIN),
                "-f", "bytes",
                "--noCategories",
                "-j", str(tmp_path),
                *[str(pid) for pid in pids],
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=FOOTPRINT_TIMEOUT_SECONDS,
            check=False,
        )
        if not tmp_path.exists():
            return {}
        data = json.loads(tmp_path.read_text())
    except (subprocess.SubprocessError, OSError, ValueError):
        return {}
    finally:
        tmp_path.unlink(missing_ok=True)

    footprints: dict[int, int] = {}
    for entry in data.get("processes", []):
        pid = entry.get("pid")
        phys = entry.get("auxiliary", {}).get("phys_footprint")
        if pid is not None and phys is not None:
            footprints[int(pid)] = int(phys)
    return footprints


def classify_tag(name: str, cmdline: list[str]) -> str:
    """Classify a non-root process into a coarse tag.

    Matches known language server names against the process name and its
    full command line (so e.g. `node .../tsserver.js` is caught even
    though the process name itself is just "node").
    """
    haystack = " ".join([name, *cmdline]).lower()
    for marker in KNOWN_LANGUAGE_SERVERS:
        if marker in haystack:
            return TAG_LANGUAGE_SERVER
    return TAG_CHILD_OTHER


@dataclass
class ProcessReading:
    pid: int
    tag: str
    name: str
    rss_bytes: int
    cpu_percent: float
    footprint_bytes: int | None = None  # phys_footprint; None if unobtainable


@dataclass
class Sample:
    t: float  # seconds elapsed since the sampler started
    timestamp: float  # wall-clock unix time
    tree_rss_bytes: int
    tree_cpu_percent: float
    tree_footprint_bytes: int | None = None  # sum of footprint_bytes; None if unobtainable
    processes: list[ProcessReading] = field(default_factory=list)


class ProcessTreeSampler:
    """Samples a root PID and its recursive children at a fixed interval.

    Runs in a background thread so callers can poll `.samples` (e.g. to
    evaluate the startup heuristic) while sampling continues.
    """

    def __init__(self, root_pid: int, interval: float = 1.0) -> None:
        self.root_pid = root_pid
        self.interval = interval
        self.samples: list[Sample] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_time: float | None = None
        self._primed_pids: set[int] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval * 2 + 1)

    def is_root_alive(self) -> bool:
        return psutil.pid_exists(self.root_pid)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            sample = self._take_sample()
            if sample is not None:
                with self._lock:
                    self.samples.append(sample)
            if self._stop_event.wait(self.interval):
                break

    def _take_sample(self) -> Sample | None:
        assert self._start_time is not None
        try:
            root = psutil.Process(self.root_pid)
            procs = [root, *root.children(recursive=True)]
        except psutil.NoSuchProcess:
            return None

        footprints: dict[int, int] = {}
        if footprint_available():
            footprints = sample_footprints([proc.pid for proc in procs])

        readings: list[ProcessReading] = []
        for proc in procs:
            try:
                if proc.pid not in self._primed_pids:
                    # First cpu_percent() call for a pid has no baseline
                    # and always returns 0.0 — prime it here so the next
                    # sample reports a meaningful value.
                    proc.cpu_percent(None)
                    self._primed_pids.add(proc.pid)
                    cpu = 0.0
                else:
                    cpu = proc.cpu_percent(None)
                with proc.oneshot():
                    rss = proc.memory_info().rss
                    name = proc.name()
                    try:
                        cmdline = proc.cmdline()
                    except (psutil.AccessDenied, psutil.ZombieProcess):
                        cmdline = []
                tag = TAG_ZED if proc.pid == self.root_pid else classify_tag(name, cmdline)
                readings.append(
                    ProcessReading(
                        pid=proc.pid,
                        tag=tag,
                        name=name,
                        rss_bytes=rss,
                        cpu_percent=cpu,
                        footprint_bytes=footprints.get(proc.pid),
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if not readings:
            return None

        footprint_readings = [r.footprint_bytes for r in readings if r.footprint_bytes is not None]
        tree_footprint_bytes = sum(footprint_readings) if footprint_readings else None

        elapsed = time.monotonic() - self._start_time
        return Sample(
            t=elapsed,
            timestamp=time.time(),
            tree_rss_bytes=sum(r.rss_bytes for r in readings),
            tree_cpu_percent=sum(r.cpu_percent for r in readings),
            tree_footprint_bytes=tree_footprint_bytes,
            processes=readings,
        )

    def snapshot(self) -> list[Sample]:
        """Return a shallow copy of the samples collected so far."""
        with self._lock:
            return list(self.samples)
