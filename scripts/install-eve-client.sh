#!/usr/bin/env bash
set -euo pipefail

PACKAGE_SPEC="eve-memory-client"
BINARY_NAME="eve"
INSTALL_METHOD=""

expected_binary_for_method() {
  local method="$1"

  if [ "$method" = "uv" ] && command -v uv >/dev/null 2>&1; then
    local uv_bin_dir
    uv_bin_dir="$(uv tool dir --bin)"
    if [ -x "$uv_bin_dir/$BINARY_NAME" ]; then
      printf '%s\n' "$uv_bin_dir/$BINARY_NAME"
      return 0
    fi
  fi

  if [ "$method" = "pipx" ] && command -v pipx >/dev/null 2>&1; then
    local pipx_bin_dir
    pipx_bin_dir="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
    if [ -n "$pipx_bin_dir" ] && [ -x "$pipx_bin_dir/$BINARY_NAME" ]; then
      printf '%s\n' "$pipx_bin_dir/$BINARY_NAME"
      return 0
    fi
  fi

  return 1
}

resolve_installed_binary() {
  expected_binary_for_method "$INSTALL_METHOD"
}

handle_shadowed_binary() {
  local expected_binary="$1"
  local path_binary

  if ! command -v "$BINARY_NAME" >/dev/null 2>&1; then
    return 0
  fi

  path_binary="$(command -v "$BINARY_NAME")"
  if [ "$path_binary" = "$expected_binary" ]; then
    return 0
  fi

  echo "SECURITY WARNING: your shell currently resolves $BINARY_NAME to $path_binary, not $expected_binary." >&2
  echo "Run 'command -v $BINARY_NAME' after updating PATH to confirm the active binary." >&2
  echo "Aborting because a conflicting $BINARY_NAME binary is ahead of the installed one on PATH." >&2
  echo "Update PATH so the installed Eve client comes first, then run 'eve connect'." >&2
  exit 1
}

if command -v uv >/dev/null 2>&1; then
  INSTALL_METHOD="uv"
elif command -v pipx >/dev/null 2>&1; then
  INSTALL_METHOD="pipx"
else
  echo "No supported installer found. Install uv or pipx first." >&2
  exit 1
fi

if EXPECTED_BINARY="$(expected_binary_for_method "$INSTALL_METHOD")"; then
  handle_shadowed_binary "$EXPECTED_BINARY"
fi

if [ "$INSTALL_METHOD" = "uv" ]; then
  uv tool install "$PACKAGE_SPEC"
elif [ "$INSTALL_METHOD" = "pipx" ]; then
  pipx install "$PACKAGE_SPEC"
fi

if INSTALLED_BINARY="$(resolve_installed_binary)"; then
  "$INSTALLED_BINARY" version >/dev/null
  echo "Installed executable: $INSTALLED_BINARY"
  handle_shadowed_binary "$INSTALLED_BINARY"
else
  echo "Installed, but could not locate the expected '$BINARY_NAME' executable for install method '$INSTALL_METHOD'." >&2
  exit 1
fi

echo
echo "Eve client installed."
echo "Next:"
echo "  eve connect"
