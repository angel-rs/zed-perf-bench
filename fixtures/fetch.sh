#!/usr/bin/env bash
# Fetch fixtures for zed-perf-bench: pinned depth-1 clones of real-world
# projects, plus generated large files. Idempotent — skips anything that
# already exists.
#
# SHAs below were the HEAD of each repo's default branch on 2026-08-11
# (fetched via `git ls-remote <repo> HEAD`). They are pinned so that a
# benchmark run is reproducible independent of upstream commit history.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VSCODE_SHA="0f0db4fbe776e2e35ce9a73e9ff396e2705a9e1e"
ZED_SHA="d71f1461045c098dc6ca6b1b5adcf1b8949722e8"

clone_pinned() {
  local name="$1" url="$2" sha="$3"
  if [ -d "$name/.git" ]; then
    echo "==> $name already present, skipping"
    return
  fi
  echo "==> cloning $name @ $sha"
  rm -rf "$name"
  mkdir -p "$name"
  (
    cd "$name"
    git init -q
    git remote add origin "$url"
    git fetch --depth 1 origin "$sha"
    git checkout -q FETCH_HEAD
  )
}

clone_pinned "vscode" "https://github.com/microsoft/vscode.git" "$VSCODE_SHA"
clone_pinned "zed" "https://github.com/zed-industries/zed.git" "$ZED_SHA"

mkdir -p large

if [ ! -f large/100mb.log ]; then
  echo "==> generating large/100mb.log"
  LOG_LINE='2026-08-11T06:00:00.000Z INFO  [worker-07] request_id=a1b2c3d4 handled GET /api/v1/status in 12ms status=200 bytes=482'
  # Each line is ~140 bytes; ~750k lines gets us to ~100MB.
  python3 - "$LOG_LINE" <<'PY'
import sys

line_template = sys.argv[1]
target_bytes = 100 * 1024 * 1024
with open("large/100mb.log", "w") as f:
    written = 0
    i = 0
    while written < target_bytes:
        line = f"{line_template} seq={i}\n"
        f.write(line)
        written += len(line)
        i += 1
PY
else
  echo "==> large/100mb.log already present, skipping"
fi

if [ ! -f large/10mb-single-line.json ]; then
  echo "==> generating large/10mb-single-line.json"
  python3 - <<'PY'
import json

target_bytes = 10 * 1024 * 1024
records = []
size = 0
i = 0
while size < target_bytes:
    record = {
        "id": i,
        "name": f"item-{i}",
        "tags": ["fixture", "large", "single-line"],
        "value": i * 1.5,
        "active": i % 2 == 0,
    }
    records.append(record)
    size += len(json.dumps(record))
    i += 1

with open("large/10mb-single-line.json", "w") as f:
    json.dump({"records": records}, f, separators=(",", ":"))
PY
else
  echo "==> large/10mb-single-line.json already present, skipping"
fi

echo "==> done"
