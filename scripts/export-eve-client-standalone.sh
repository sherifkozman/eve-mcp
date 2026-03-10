#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SOURCE_DIR="$ROOT_DIR/packages/client"
TARGET_DIR="${1:-}"
TARGET_REPO_URL="${EVE_CLIENT_TARGET_REPO_URL:-https://github.com/sherifkozman/eve-mcp}"
TARGET_ISSUES_URL="${EVE_CLIENT_TARGET_ISSUES_URL:-https://github.com/sherifkozman/eve-mcp/issues}"

if [ -z "$TARGET_DIR" ]; then
  echo "Usage: $0 /path/to/standalone-eve-client-repo" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"

rsync -a \
  --delete \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  --exclude 'dist' \
  --exclude 'build' \
  --exclude 'release' \
  "$SOURCE_DIR"/ "$TARGET_DIR"/

mkdir -p "$TARGET_DIR/.github/workflows"
cp "$SOURCE_DIR/repo-template/release-eve-client.yml" "$TARGET_DIR/.github/workflows/release-eve-client.yml"

python3 - <<'PY' "$TARGET_DIR/pyproject.toml" "$TARGET_REPO_URL" "$TARGET_ISSUES_URL"
from pathlib import Path
import sys

pyproject = Path(sys.argv[1])
repo_url = sys.argv[2]
issues_url = sys.argv[3]
text = pyproject.read_text()
text = text.replace('Repository = "https://github.com/sherifkozman/eve"', f'Repository = "{repo_url}"')
text = text.replace('Issues = "https://github.com/sherifkozman/eve/issues"', f'Issues = "{issues_url}"')
pyproject.write_text(text)
PY

echo "Exported standalone eve-client repo to $TARGET_DIR"
