#!/usr/bin/env bash
SCRIPT_NAME="$(basename "${0:-sh}")"
case "$SCRIPT_NAME" in
  sh|bash|dash|zsh|-*)
    if [ ! -t 0 ]; then
      printf '%s\n' "Do not pipe this installer directly to sh." >&2
      echo "Download it first, inspect it, then run:" >&2
      echo "  sh install.sh" >&2
      exit 1
    fi
    ;;
esac

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

# -----------------------------------------------------------------------------
# Eve CLI Standalone Installer
# -----------------------------------------------------------------------------
# Installs Eve CLI from PyPI using an existing uv or pipx installation.
# This script intentionally does not bootstrap package-manager tooling.
# -----------------------------------------------------------------------------

PACKAGE_SPEC="eve-memory-client"
BINARY_NAME="eve"
INSTALL_METHOD=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

  echo -e "${YELLOW}SECURITY WARNING: your shell currently resolves $BINARY_NAME to $path_binary, not $expected_binary.${NC}" >&2
  echo -e "${YELLOW}Run 'command -v $BINARY_NAME' after updating PATH to confirm the active binary.${NC}" >&2
  echo -e "${RED}Aborting because a conflicting $BINARY_NAME binary is ahead of the installed one on PATH.${NC}" >&2
  echo -e "${RED}Update PATH so the installed Eve client comes first, then run 'eve connect'.${NC}" >&2
  exit 1
}

echo -e "${BLUE}==> Installing Eve CLI...${NC}"

if command -v uv >/dev/null 2>&1; then
  INSTALL_METHOD="uv"
  echo -e "${GREEN}✓ Found uv at $(command -v uv)${NC}"
elif command -v pipx >/dev/null 2>&1; then
  INSTALL_METHOD="pipx"
  echo -e "${GREEN}✓ Found pipx at $(command -v pipx)${NC}"
else
  echo -e "${RED}No supported installer found. Install uv or pipx first.${NC}" >&2
  exit 1
fi

if EXPECTED_BINARY="$(expected_binary_for_method "$INSTALL_METHOD")"; then
  handle_shadowed_binary "$EXPECTED_BINARY"
fi

if [ "$INSTALL_METHOD" = "uv" ]; then
  echo -e "${BLUE}==> Running: uv tool install $PACKAGE_SPEC${NC}"
  uv tool install "$PACKAGE_SPEC"
elif [ "$INSTALL_METHOD" = "pipx" ]; then
  echo -e "${BLUE}==> Running: pipx install $PACKAGE_SPEC${NC}"
  pipx install "$PACKAGE_SPEC"
fi

if INSTALLED_BINARY="$(expected_binary_for_method "$INSTALL_METHOD")"; then
  "$INSTALLED_BINARY" version >/dev/null
  echo -e "${GREEN}✓ Eve client installed to: $INSTALLED_BINARY${NC}"
  handle_shadowed_binary "$INSTALLED_BINARY"
else
  echo -e "${RED}Installed, but could not locate the expected '$BINARY_NAME' executable for install method '$INSTALL_METHOD'.${NC}" >&2
  exit 1
fi

echo
echo -e "${GREEN}Eve client installation complete!${NC}"
echo "Next steps:"
echo "  eve connect"
