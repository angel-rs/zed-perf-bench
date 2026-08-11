#!/usr/bin/env bash
# Fetch fixtures for zed-perf-bench: pinned depth-1 clones of real-world
# projects, plus generated large files. Idempotent — skips anything that
# already exists.
#
# SHAs below were the HEAD of each repo's default branch on 2026-08-11
# (fetched via `git ls-remote <repo> HEAD`). They are pinned so that a
# benchmark run is reproducible independent of upstream commit history.
#
# --link-zed <path> / --link-vscode <path> symlink fixtures/zed or
# fixtures/vscode to an existing local git clone instead of cloning from
# GitHub — zero new downloads for a local baseline run. The target is
# used as-is, on whatever commit it happens to be checked out to (not
# pinned); zpb/scenario.py's fixture_git_sha / fixture_dirty result
# fields record that commit per-run so a local-fixture run stays
# auditable. See BASELINE.md and README.md "Using existing local
# checkouts".
set -euo pipefail

ORIG_PWD="$PWD"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VSCODE_SHA="0f0db4fbe776e2e35ce9a73e9ff396e2705a9e1e"
ZED_SHA="d71f1461045c098dc6ca6b1b5adcf1b8949722e8"

LINK_ZED=""
LINK_VSCODE=""

usage() {
  cat <<'EOF'
Usage: fetch.sh [--link-zed <path>] [--link-vscode <path>]

  --link-zed <path>     Symlink fixtures/zed to an existing local git
                         clone instead of cloning from GitHub.
  --link-vscode <path>  Symlink fixtures/vscode to an existing local git
                         clone instead of cloning from GitHub.

Without these flags, fetch.sh clones the pinned depth-1 checkouts as
before. Large-file fixtures (large/*.log, large/*.json) are always
generated locally regardless of these flags — no network involved.
EOF
}

# Resolve a possibly-relative path against the directory fetch.sh was
# invoked from (ORIG_PWD), not $SCRIPT_DIR, which we've already cd'd
# into above. The shell invoking fetch.sh normally expands a leading
# "~" before we ever see it; the case arm here is defensive for the
# rare case it arrives unexpanded (e.g. a quoted argument from another
# script).
resolve_path() {
  local p="$1"
  case "$p" in
    "~"|"~/"*) p="${HOME}${p#\~}" ;;
  esac
  case "$p" in
    /*) printf '%s\n' "$p" ;;
    *) printf '%s\n' "$ORIG_PWD/$p" ;;
  esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    --link-zed)
      [ $# -ge 2 ] || { echo "error: --link-zed requires a path argument" >&2; exit 1; }
      LINK_ZED="$(resolve_path "$2")"
      shift 2
      ;;
    --link-vscode)
      [ $# -ge 2 ] || { echo "error: --link-vscode requires a path argument" >&2; exit 1; }
      LINK_VSCODE="$(resolve_path "$2")"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

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

link_fixture() {
  local name="$1" target="$2"
  if [ -e "$name" ] || [ -L "$name" ]; then
    echo "==> $name already present (dir or symlink), skipping"
    return
  fi
  if [ ! -d "$target" ]; then
    echo "error: --link-$name target '$target' does not exist or is not a directory" >&2
    exit 1
  fi
  if [ ! -e "$target/.git" ]; then
    echo "error: --link-$name target '$target' is not a git repository (no .git)" >&2
    exit 1
  fi
  echo "==> linking $name -> $target (zero new download)"
  ln -s "$target" "$name"
}

if [ -n "$LINK_VSCODE" ]; then
  link_fixture "vscode" "$LINK_VSCODE"
else
  clone_pinned "vscode" "https://github.com/microsoft/vscode.git" "$VSCODE_SHA"
fi

if [ -n "$LINK_ZED" ]; then
  link_fixture "zed" "$LINK_ZED"
else
  clone_pinned "zed" "https://github.com/zed-industries/zed.git" "$ZED_SHA"
fi

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
