#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PACKAGE_DIR="$ROOT_DIR/packages/client"
OUT_DIR="${EVE_CLIENT_RELEASE_OUT_DIR:-$ROOT_DIR/release/eve-client}"
PYINSTALLER="${EVE_CLIENT_PYINSTALLER:-uv run --package eve-client --python 3.11 --with pyinstaller pyinstaller}"

VERSION="$(python3 - <<'PY'
from pathlib import Path
import tomllib

data = tomllib.loads(Path("packages/client/pyproject.toml").read_text())
print(data["project"]["version"])
PY
)"

DIST_DIR="$OUT_DIR/dist"
BIN_DIR="$OUT_DIR/bin"
ARCHIVE_DIR="$OUT_DIR/artifacts"
BUILD_DIR="$OUT_DIR/.build"

rm -rf "$DIST_DIR" "$BIN_DIR" "$ARCHIVE_DIR" "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BIN_DIR" "$ARCHIVE_DIR" "$BUILD_DIR"

cd "$ROOT_DIR"

uv build "$PACKAGE_DIR"
cp dist/eve_client-"$VERSION".tar.gz "$DIST_DIR/"
cp dist/eve_client-"$VERSION"-py3-none-any.whl "$DIST_DIR/"

build_binary() {
  local name="$1"
  local entry="$2"
  local build_subdir="$BUILD_DIR/$name"

  mkdir -p "$build_subdir"
  # shellcheck disable=SC2086
  $PYINSTALLER \
    --clean \
    --noconfirm \
    --onefile \
    --name "$name" \
    --add-data "$PACKAGE_DIR/pyproject.toml:." \
    --distpath "$BIN_DIR" \
    --workpath "$build_subdir/work" \
    --specpath "$build_subdir/spec" \
    "$entry"
}

build_binary "eve" "$PACKAGE_DIR/eve_client/__main__.py"
build_binary "eve-claude-hook" "$PACKAGE_DIR/eve_client/claude_hook_entry.py"
build_binary "eve-gemini-hook" "$PACKAGE_DIR/eve_client/gemini_hook_entry.py"

cp "$PACKAGE_DIR/README.md" "$ARCHIVE_DIR/README.md"
cp "$DIST_DIR"/eve_client-"$VERSION".tar.gz "$ARCHIVE_DIR/"
cp "$DIST_DIR"/eve_client-"$VERSION"-py3-none-any.whl "$ARCHIVE_DIR/"
cp "$BIN_DIR"/eve "$ARCHIVE_DIR/"
cp "$BIN_DIR"/eve-claude-hook "$ARCHIVE_DIR/"
cp "$BIN_DIR"/eve-gemini-hook "$ARCHIVE_DIR/"

(
  cd "$ARCHIVE_DIR"
  tar -czf "eve-client-${VERSION}-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m).tar.gz" \
    README.md \
    eve \
    eve-claude-hook \
    eve-gemini-hook \
    eve_client-"$VERSION".tar.gz \
    eve_client-"$VERSION"-py3-none-any.whl
)

echo "Built Eve client release artifacts in $OUT_DIR"
