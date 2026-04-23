#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# Eve CLI Standalone Installer
# -----------------------------------------------------------------------------
# Installs Eve CLI using `uv` (fast Python package installer).
# If `uv` is not found, it temporarily installs it to bootstrap Eve CLI.
# -----------------------------------------------------------------------------

SOURCE="${EVE_CLIENT_SOURCE:-git+https://github.com/sherifkozman/eve-mcp.git}"
EXTRA_FLAGS="${EVE_CLIENT_INSTALL_FLAGS:-}"
BINARY_NAME="${EVE_CLIENT_BINARY:-eve}"
ALLOW_SHADOWED_BINARY="${EVE_CLIENT_ALLOW_SHADOWED_BINARY:-0}"
FAIL_ON_SHADOWED_BINARY="${EVE_CLIENT_FAIL_ON_SHADOWED_BINARY:-0}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}==> Installing Eve CLI...${NC}"

# Find or install `uv`
UV_BIN=""
if command -v uv >/dev/null 2>&1; then
    UV_BIN="uv"
    echo -e "${GREEN}✓ Found uv at $(command -v uv)${NC}"
else
    echo -e "${YELLOW}uv not found. Installing uv (fast Python package installer) to bootstrap Eve CLI...${NC}"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    
    # Try common uv install paths
    if [ -x "$HOME/.local/bin/uv" ]; then
        UV_BIN="$HOME/.local/bin/uv"
    elif [ -x "$HOME/.cargo/bin/uv" ]; then
        UV_BIN="$HOME/.cargo/bin/uv"
    fi

    if [ -z "$UV_BIN" ]; then
        echo -e "${RED}Failed to locate installed uv binary. Aborting.${NC}" >&2
        exit 1
    fi
    echo -e "${GREEN}✓ Installed uv at $UV_BIN${NC}"
fi

echo -e "${BLUE}==> Running: $UV_BIN tool install $SOURCE ${EXTRA_FLAGS}${NC}"
# shellcheck disable=SC2086
"$UV_BIN" tool install $EXTRA_FLAGS "$SOURCE"

# Locate the installed eve binary
UV_BIN_DIR="$("$UV_BIN" tool dir --bin)"
INSTALLED_BINARY="$UV_BIN_DIR/$BINARY_NAME"

if [ ! -x "$INSTALLED_BINARY" ]; then
    echo -e "${RED}Installed, but could not locate the expected '$BINARY_NAME' executable in $UV_BIN_DIR.${NC}" >&2
    exit 1
fi

echo -e "${GREEN}✓ Eve client installed to: $INSTALLED_BINARY${NC}"

# Check for shadowed binary or missing PATH
if command -v "$BINARY_NAME" >/dev/null 2>&1; then
    PATH_BINARY="$(command -v "$BINARY_NAME")"
    if [ "$PATH_BINARY" != "$INSTALLED_BINARY" ]; then
        echo -e "${YELLOW}SECURITY WARNING: your shell currently resolves $BINARY_NAME to $PATH_BINARY, not $INSTALLED_BINARY.${NC}" >&2
        echo -e "${YELLOW}Run 'command -v $BINARY_NAME' after updating PATH to confirm the active binary.${NC}" >&2
        if [ "$FAIL_ON_SHADOWED_BINARY" = "1" ]; then
            echo -e "${RED}Aborting because EVE_CLIENT_FAIL_ON_SHADOWED_BINARY=1 is set.${NC}" >&2
            exit 1
        fi
        if [ "$ALLOW_SHADOWED_BINARY" != "1" ] && [ -t 0 ] && [ -t 1 ]; then
            printf "Proceed anyway and keep the current PATH order? [y/N] " >&2
            read -r proceed_shadowed
            case "${proceed_shadowed}" in
                y|Y|yes|YES)
                    # Proceed
                    ;;
                *)
                    echo -e "${RED}Aborting because the active shell would still execute a different $BINARY_NAME binary.${NC}" >&2
                    exit 1
                    ;;
            esac
        fi
    fi
else
    echo -e "\n${YELLOW}Warning: '$UV_BIN_DIR' is not in your PATH.${NC}"
    echo -e "To use '$BINARY_NAME', add it to your PATH. For example, add this to your ~/.bashrc or ~/.zshrc:"
    echo -e "  export PATH=\"\$PATH:$UV_BIN_DIR\""
fi

echo
echo -e "${GREEN}Eve client installation complete!${NC}"
echo "Next steps:"
echo "  $BINARY_NAME quickstart"
