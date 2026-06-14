#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PACKAGE_DIR="$ROOT_DIR/packages/client"
DIST_DIR="${EVE_CLIENT_DIST_DIR:-$ROOT_DIR/dist}"
MODE="dry-run"
SKIP_BUILD="0"

usage() {
  cat <<'EOF'
Usage: publish-eve-client-pypi.sh [--dry-run|--publish] [--skip-build] [--dist-dir DIR]

Builds and validates eve-memory-client PyPI artifacts. --dry-run never uploads.
--publish uploads existing validated artifacts and requires PYPI_API_TOKEN.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --publish)
      MODE="publish"
      shift
      ;;
    --skip-build)
      SKIP_BUILD="1"
      shift
      ;;
    --dist-dir)
      if [ "$#" -lt 2 ]; then
        echo "--dist-dir requires a value" >&2
        exit 2
      fi
      DIST_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$DIST_DIR"

if [ "$SKIP_BUILD" != "1" ]; then
  rm -f "$DIST_DIR"/eve_memory_client-*.tar.gz "$DIST_DIR"/eve_memory_client-*-py3-none-any.whl
  uv build "$PACKAGE_DIR" --out-dir "$DIST_DIR"
fi

shopt -s nullglob
ARTIFACTS=("$DIST_DIR"/eve_memory_client-*.tar.gz "$DIST_DIR"/eve_memory_client-*-py3-none-any.whl)
shopt -u nullglob

if [ "${#ARTIFACTS[@]}" -lt 2 ]; then
  echo "Expected eve-memory-client sdist and wheel in $DIST_DIR" >&2
  exit 1
fi

uvx twine check "${ARTIFACTS[@]}"

if [ "$MODE" = "publish" ]; then
  if [ -z "${PYPI_API_TOKEN:-}" ]; then
    echo "PYPI_API_TOKEN is required for --publish" >&2
    exit 1
  fi
  UV_PUBLISH_TOKEN="$PYPI_API_TOKEN" uv publish "${ARTIFACTS[@]}"
  echo "Published eve-memory-client artifacts from $DIST_DIR"
else
  echo "Dry run complete; not publishing eve-memory-client artifacts from $DIST_DIR"
fi
