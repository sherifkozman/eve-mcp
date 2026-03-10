#!/usr/bin/env bash
set -euo pipefail

SOURCE="${EVE_CLIENT_SOURCE:-eve-client}"
EXTRA_FLAGS="${EVE_CLIENT_INSTALL_FLAGS:-}"
BINARY_NAME="${EVE_CLIENT_BINARY:-eve}"
FAIL_ON_SHADOWED_BINARY="${EVE_CLIENT_FAIL_ON_SHADOWED_BINARY:-0}"
ALLOW_SHADOWED_BINARY="${EVE_CLIENT_ALLOW_SHADOWED_BINARY:-0}"
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

  if [ "$method" = "pip" ] && command -v python3 >/dev/null 2>&1; then
    local user_base
    user_base="$(python3 -m site --user-base 2>/dev/null || true)"
    if [ -n "$user_base" ]; then
      printf '%s\n' "$user_base/bin/$BINARY_NAME"
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
  if [ "$FAIL_ON_SHADOWED_BINARY" = "1" ]; then
    if [ "$ALLOW_SHADOWED_BINARY" = "1" ]; then
      echo "EVE_CLIENT_FAIL_ON_SHADOWED_BINARY=1 overrides EVE_CLIENT_ALLOW_SHADOWED_BINARY=1." >&2
    fi
    echo "Aborting because EVE_CLIENT_FAIL_ON_SHADOWED_BINARY=1 is set." >&2
    exit 1
  fi
  if [ "$ALLOW_SHADOWED_BINARY" = "1" ]; then
    echo "Proceeding because EVE_CLIENT_ALLOW_SHADOWED_BINARY=1 is set." >&2
    return 0
  fi
  if [ -t 0 ] && [ -t 1 ]; then
    printf "Proceed anyway and keep the current PATH order? [y/N] " >&2
    read -r proceed_shadowed
    case "${proceed_shadowed}" in
      y|Y|yes|YES)
        return 0
        ;;
      *)
        echo "Aborting because the active shell would still execute a different $BINARY_NAME binary." >&2
        exit 1
        ;;
    esac
  fi

  echo "Aborting because a conflicting $BINARY_NAME binary is ahead of the installed one on PATH." >&2
  echo "Set EVE_CLIENT_ALLOW_SHADOWED_BINARY=1 to override in non-interactive mode." >&2
  exit 1
}

if command -v uv >/dev/null 2>&1; then
  INSTALL_METHOD="uv"
elif command -v pipx >/dev/null 2>&1; then
  INSTALL_METHOD="pipx"
elif command -v python3 >/dev/null 2>&1; then
  INSTALL_METHOD="pip"
else
  echo "No supported installer found. Install uv, pipx, or python3 first." >&2
  exit 1
fi

if EXPECTED_BINARY="$(expected_binary_for_method "$INSTALL_METHOD")"; then
  handle_shadowed_binary "$EXPECTED_BINARY"
fi

if [ "$INSTALL_METHOD" = "uv" ]; then
  # shellcheck disable=SC2086
  uv tool install $EXTRA_FLAGS "$SOURCE"
elif [ "$INSTALL_METHOD" = "pipx" ]; then
  # shellcheck disable=SC2086
  pipx install $EXTRA_FLAGS "$SOURCE"
else
  # shellcheck disable=SC2086
  python3 -m pip install --user $EXTRA_FLAGS "$SOURCE"
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
echo "  eve quickstart"
