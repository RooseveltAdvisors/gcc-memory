#!/usr/bin/env bash
# Thin wrapper that runs the Python backfill script through uv so we never depend on pip.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKFILL_PY="$ROOT/scripts/backfill_history.py"
export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install it from https://docs.astral.sh/uv/ and retry." >&2
  exit 1
fi

if [ ! -f "$BACKFILL_PY" ]; then
  echo "Missing scripts/backfill_history.py; are you running from the repo root?" >&2
  exit 1
fi

if [ $# -lt 1 ]; then
  cat <<'USAGE' >&2
Usage: scripts/run_backfill.sh /path/to/workspace [--branch main] [--dry-run] [...]

All extra arguments are passed directly to scripts/backfill_history.py so you can
add --dry-run, point at alternate history files, etc.
USAGE
  exit 1
fi

WORKSPACE="$1"
shift
EXTRA_ARGS=("$@")
BRANCH=""
IDX=0
COUNT=${#EXTRA_ARGS[@]}
while [ $IDX -lt $COUNT ]; do
  arg="${EXTRA_ARGS[$IDX]}"
  if [ "$arg" = "--branch" ] && [ $((IDX + 1)) -lt $COUNT ]; then
    BRANCH="${EXTRA_ARGS[$((IDX + 1))]}"
    IDX=$((IDX + 2))
    continue
  fi
  case "$arg" in
    --branch=*)
      BRANCH="${arg#*=}"
      ;;
  esac
  IDX=$((IDX + 1))
done

if [ ! -d "$WORKSPACE" ]; then
  echo "Workspace '$WORKSPACE' does not exist" >&2
  exit 1
fi

cd "$ROOT"
uv run "$BACKFILL_PY" "$WORKSPACE" "${EXTRA_ARGS[@]}"
uv run "$ROOT/scripts/update_main.py" "$WORKSPACE" ${BRANCH:+--branch "$BRANCH"}
