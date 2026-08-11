"""Process-tree sampler.

Samples a root process and all of its descendants at a fixed interval
(default 1 Hz), recording per-process RSS and CPU usage plus a coarse
"tag" derived from the process name/cmdline (e.g. "language-server").
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

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


@dataclass
class Sample:
    t: float  # seconds elapsed since the sampler started
    timestamp: float  # wall-clock unix time
    tree_rss_bytes: int
    tree_cpu_percent: float
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
                    ProcessReading(pid=proc.pid, tag=tag, name=name, rss_bytes=rss, cpu_percent=cpu)
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if not readings:
            return None

        elapsed = time.monotonic() - self._start_time
        return Sample(
            t=elapsed,
            timestamp=time.time(),
            tree_rss_bytes=sum(r.rss_bytes for r in readings),
            tree_cpu_percent=sum(r.cpu_percent for r in readings),
            processes=readings,
        )

    def snapshot(self) -> list[Sample]:
        """Return a shallow copy of the samples collected so far."""
        with self._lock:
            return list(self.samples)
